"""
execution/ab_title_test.py
Title A/B Testing Engine

After a video has been live for 24+ hours, checks its view count.
If views are below the threshold (indicating poor CTR/impressions),
generates a fresh title and swaps it via the YouTube Data API.

Schedule: Run daily, 10 hours after each upload window.
The scheduler.py calls this as a subprocess.
"""

import os
import sys
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import init_db, get_pending_ab_tests, resolve_ab_test, save_ab_test
from core.telegram_bot import send_message
from core.gemini_client import generate_with_rotation

# ── Constants ─────────────────────────────────────────────────────────────────
# If a video has fewer than this many views after 24 hours, swap the title
LOW_VIEWS_THRESHOLD = 15


def _get_youtube_client(channel_name: str):
    """Build authenticated YouTube API client for a given channel."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        token_path = os.path.join("channels", f"{channel_name}_token.json")
        if not os.path.exists(token_path):
            print(f"[ABTest] No token for {channel_name}, skipping.")
            return None

        SCOPES = ["https://www.googleapis.com/auth/youtube"]
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        print(f"[ABTest] YouTube auth failed for {channel_name}: {e}")
        return None


def _get_video_views(youtube, video_id: str) -> int:
    """Fetch current view count for a video."""
    try:
        resp = youtube.videos().list(
            part="statistics",
            id=video_id
        ).execute()
        items = resp.get("items", [])
        if not items:
            return -1
        return int(items[0]["statistics"].get("viewCount", 0))
    except Exception as e:
        print(f"[ABTest] Failed to get views for {video_id}: {e}")
        return -1


def _generate_title_b(title_a: str, channel_name: str) -> str:
    """Use AI rotation (Groq → Gemini fallback) to generate an alternative title."""
    prompt = f"""You are a viral YouTube Shorts title optimizer.

The current title is performing poorly (low views in first 24 hours):
"{title_a}"

Generate ONE alternative title that:
1. Covers the exact same story/topic
2. Uses a completely different psychological angle (switch between: shocking question, bold claim, controversy, fear, secret reveal)
3. Is under 80 characters
4. Does NOT start with the same first word as the original
5. Uses stronger emotional triggers

Return ONLY the new title text. No quotes, no explanation.
Channel niche: {channel_name.replace('_', ' ').title()}"""

    try:
        result = generate_with_rotation(prompt).strip().strip('"')
        return result if result else ""
    except Exception as e:
        print(f"[ABTest] Title generation failed: {e}")
        return ""


def _update_video_title(youtube, video_id: str, new_title: str) -> bool:
    """Update the video title via YouTube Data API."""
    try:
        # First fetch current snippet to preserve other fields
        resp = youtube.videos().list(part="snippet", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            return False
        snippet = items[0]["snippet"]
        snippet["title"] = new_title[:100]  # YouTube title limit

        youtube.videos().update(
            part="snippet",
            body={"id": video_id, "snippet": snippet}
        ).execute()
        return True
    except Exception as e:
        print(f"[ABTest] Failed to update title for {video_id}: {e}")
        return False


def run_ab_title_tests():
    """
    Main function — check all pending A/B tests and swap titles where needed.
    """
    print("\n[ABTest] === Title A/B Test Runner Starting ===")
    init_db()

    pending = get_pending_ab_tests(min_age_hours=24.0)
    if not pending:
        print("[ABTest] No pending A/B tests to evaluate.")
        return

    print(f"[ABTest] Found {len(pending)} pending test(s).")

    for test in pending:
        test_id     = test["id"]
        channel     = test["channel"]
        video_id    = test["video_id"]
        title_a     = test["title_a"]
        title_b     = test["title_b"]

        print(f"\n[ABTest] Evaluating: {video_id} (channel: {channel})")
        print(f"[ABTest] Title A: '{title_a}'")

        youtube = _get_youtube_client(channel)
        if not youtube:
            continue

        views = _get_video_views(youtube, video_id)
        print(f"[ABTest] Views after 24h: {views}")

        if views < 0:
            print(f"[ABTest] Could not fetch views. Skipping test {test_id}.")
            continue

        if views >= LOW_VIEWS_THRESHOLD:
            # Title A is performing well — keep it
            print(f"[ABTest] Title A performing OK ({views} views). Keeping original.")
            resolve_ab_test(test_id, switched=False)
            continue

        # Title A is underperforming — switch to Title B
        if not title_b:
            title_b = _generate_title_b(title_a, channel)

        if not title_b:
            print(f"[ABTest] Could not generate Title B. Skipping switch.")
            resolve_ab_test(test_id, switched=False)
            continue

        print(f"[ABTest] Title A underperforming. Switching to Title B: '{title_b}'")
        success = _update_video_title(youtube, video_id, title_b)

        if success:
            resolve_ab_test(test_id, switched=True)
            send_message(
                f"🔄 *A/B Title Switch* — {channel}\n"
                f"Video: `{video_id}` ({views} views in 24h)\n"
                f"❌ *Was*: {title_a}\n"
                f"✅ *Now*: {title_b}"
            )
            print(f"[ABTest] ✅ Title switched successfully.")
        else:
            print(f"[ABTest] ❌ Title switch API call failed.")
            resolve_ab_test(test_id, switched=False)

    print("\n[ABTest] === A/B Test Runner Complete ===")


if __name__ == "__main__":
    run_ab_title_tests()
