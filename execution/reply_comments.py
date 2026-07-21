"""
execution/reply_comments.py
Comment Reply Bot

Fetches recent unread comments on each channel's videos,
generates human-like replies using Groq, and posts them via YouTube API.

This signals to YouTube's algorithm that the creator is actively engaged,
which is a strong ranking factor for Shorts.

Safety rules:
- Never replies to its own comments (avoids loops)
- Tracks replied comment IDs in DB to prevent double-replies
- Has a daily reply cap per channel (max 15) to avoid looking spammy
- Randomizes reply timing (waits 30-90s between replies)
- Keeps replies short (1-2 sentences) and natural
"""

import os
import sys
import time
import random
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import init_db, is_comment_replied, mark_comment_replied
from core.telegram_bot import send_message
from config.settings import GROQ_API_KEYS, GROQ_MODEL

MAX_REPLIES_PER_CHANNEL = 15  # daily cap — more than this looks spammy
REPLY_DELAY_RANGE = (30, 90)  # seconds between replies (human pacing)


def _get_youtube_client(channel_name: str):
    """Build authenticated YouTube API client."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        token_path = os.path.join("channels", f"{channel_name}_token.json")
        if not os.path.exists(token_path):
            print(f"[Comments] No token for {channel_name}.")
            return None

        SCOPES = ["https://www.googleapis.com/auth/youtube"]
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        print(f"[Comments] YouTube auth failed for {channel_name}: {e}")
        return None


def _get_channel_id(youtube) -> str | None:
    """Get the authenticated channel's own channel ID."""
    try:
        resp = youtube.channels().list(part="id", mine=True).execute()
        items = resp.get("items", [])
        return items[0]["id"] if items else None
    except Exception as e:
        print(f"[Comments] Could not get channel ID: {e}")
        return None


def _get_recent_videos(youtube, channel_id: str, max_results: int = 10) -> list:
    """Fetch recent video IDs for the channel."""
    try:
        resp = youtube.search().list(
            part="id",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=max_results
        ).execute()
        return [item["id"]["videoId"] for item in resp.get("items", [])]
    except Exception as e:
        print(f"[Comments] Could not fetch recent videos: {e}")
        return []


def _get_top_comments(youtube, video_id: str, own_channel_id: str, max_results: int = 20) -> list:
    """
    Fetch top-level comments on a video, excluding the channel's own comments.
    Returns list of (comment_id, text, author_name) tuples.
    """
    comments = []
    try:
        resp = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=max_results,
            order="relevance",
            textFormat="plainText"
        ).execute()

        for thread in resp.get("items", []):
            top = thread["snippet"]["topLevelComment"]
            comment_id  = top["id"]
            author_id   = top["snippet"].get("authorChannelId", {}).get("value", "")
            author_name = top["snippet"].get("authorDisplayName", "")
            text        = top["snippet"].get("textDisplay", "")

            # Skip own comments and very short/empty comments
            if author_id == own_channel_id:
                continue
            if len(text.strip()) < 5:
                continue

            comments.append((comment_id, text[:300], author_name))
    except Exception as e:
        # commentsDisabled is common — don't treat as fatal
        if "commentsDisabled" not in str(e) and "disabled" not in str(e).lower():
            print(f"[Comments] Could not fetch comments for {video_id}: {e}")
    return comments


def _generate_reply(comment_text: str, author_name: str, channel_name: str) -> str:
    """Generate a natural, human-like reply to a comment using Groq."""
    import requests

    niche = channel_name.replace("_", " ").title()

    prompt = f"""You manage a {niche} YouTube Shorts channel. A viewer left this comment:

"{comment_text}"
— {author_name}

Write a SHORT, natural reply (1-2 sentences MAX). Rules:
- Sound like a real, enthusiastic creator, not a bot
- If it's a question, give a brief honest answer
- If it's positive, thank them briefly and add one engaging thought
- If it's negative/critical, acknowledge it respectfully
- Do NOT use emojis (they look automated)
- Do NOT start with "Great comment!" or generic openers
- Do NOT use the word "absolutely" or "certainly"
- Keep it under 25 words
- Vary your opening word each time

Return ONLY the reply text. Nothing else."""

    for key in GROQ_API_KEYS:
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.85,
                    "max_tokens": 80,
                },
                timeout=15
            )
            if resp.status_code == 200:
                reply = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
                # Safety: reject if too long or if it contains bot-like phrases
                bad_phrases = ["absolutely", "certainly", "great comment", "awesome comment", "as an ai"]
                if any(p in reply.lower() for p in bad_phrases) or len(reply) > 200:
                    continue
                return reply
        except Exception:
            continue
    return ""


def _post_reply(youtube, parent_comment_id: str, reply_text: str) -> bool:
    """Post a reply to a comment via YouTube API."""
    try:
        youtube.comments().insert(
            part="snippet",
            body={
                "snippet": {
                    "parentId": parent_comment_id,
                    "textOriginal": reply_text
                }
            }
        ).execute()
        return True
    except Exception as e:
        print(f"[Comments] Failed to post reply: {e}")
        return False


def run_comment_replies():
    """Main function — scan all active channels and reply to comments."""
    print("\n[Comments] === Comment Reply Agent Starting ===")
    init_db()

    from scheduler import get_active_channels
    channels = get_active_channels()
    if not channels:
        print("[Comments] No active channels found.")
        return

    total_replied = 0

    for channel_name in channels:
        print(f"\n[Comments] Processing channel: {channel_name.upper()}")
        youtube = _get_youtube_client(channel_name)
        if not youtube:
            continue

        own_channel_id = _get_channel_id(youtube)
        if not own_channel_id:
            continue

        video_ids = _get_recent_videos(youtube, own_channel_id, max_results=5)
        print(f"[Comments] Found {len(video_ids)} recent videos to check.")

        replies_this_channel = 0

        for video_id in video_ids:
            if replies_this_channel >= MAX_REPLIES_PER_CHANNEL:
                print(f"[Comments] Daily cap reached for {channel_name}. Stopping.")
                break

            comments = _get_top_comments(youtube, video_id, own_channel_id)
            print(f"[Comments] Video {video_id}: {len(comments)} candidate comment(s).")

            for comment_id, text, author in comments:
                if replies_this_channel >= MAX_REPLIES_PER_CHANNEL:
                    break

                # Skip already-replied comments
                if is_comment_replied(comment_id):
                    continue

                # Generate reply
                reply = _generate_reply(text, author, channel_name)
                if not reply:
                    print(f"[Comments] Could not generate reply for comment {comment_id}. Skipping.")
                    continue

                # Post reply
                success = _post_reply(youtube, comment_id, reply)
                if success:
                    mark_comment_replied(comment_id, channel_name)
                    replies_this_channel += 1
                    total_replied += 1
                    print(f"[Comments] Replied to {author}: '{reply[:60]}...'")

                    # Human-like pacing between replies
                    delay = random.randint(*REPLY_DELAY_RANGE)
                    print(f"[Comments] Waiting {delay}s before next reply...")
                    time.sleep(delay)

        print(f"[Comments] {channel_name}: {replies_this_channel} replies posted.")

    print(f"\n[Comments] === Done. Total replies posted: {total_replied} ===")
    if total_replied > 0:
        send_message(f"💬 *Comment Reply Agent*: Posted {total_replied} replies across all channels.")


if __name__ == "__main__":
    run_comment_replies()
