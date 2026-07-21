"""
youtube_uploader.py
Uploads the final video to YouTube as a Short.
Uses YouTube Data API v3 with OAuth2 — free.

Setup (one time):
1. Go to console.cloud.google.com
2. Create project → Enable YouTube Data API v3
3. Create OAuth2 credentials → Desktop App
4. Download JSON → save as config/youtube_client_secrets.json
5. Run this script once — it'll open browser for auth
6. Token saved to channels/<channel_name>_token.json — never needs re-auth

ChannelContext integration:
  Pass a ChannelContext instance to authenticate() and upload() so the uploader
  reads the correct per-channel token file without relying on os.environ.
"""

import os
import json
import random
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from config.settings import (
    YOUTUBE_CLIENT_SECRETS_FILE,
    YOUTUBE_CATEGORY_ID,
    AUTO_POST_YOUTUBE,
    CHANNELS_DIR
)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]


class YouTubeUploader:

    def __init__(self):
        self.youtube = None

    def authenticate(self, ctx=None):
        """
        OAuth2 auth — loads the per-channel token file.

        Args:
            ctx: Optional ChannelContext. When provided, reads token_path and
                 channel_name directly from the context object instead of
                 falling back to os.environ (safer for parallel execution).
        """
        creds = None
        if ctx is not None:
            channel_name = ctx.channel_name
            token_path   = ctx.token_path
        else:
            # Legacy fallback — still works with subprocess isolation model
            channel_name = os.environ.get("ACTIVE_CHANNEL", "example_channel").strip()
            token_path   = os.path.join(CHANNELS_DIR, f"{channel_name}_token.json")

        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                import time
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        creds.refresh(Request())
                        break  # Success
                    except Exception as refresh_err:
                        # Only retry on network errors (like timeouts), not on HTTP 400 Bad Request
                        if "invalid_grant" in str(refresh_err) or attempt == max_retries - 1:
                            from core.telegram_bot import send_message as _tg
                            _tg(
                                f"⚠️ *YouTube Token Refresh Failed* — {channel_name}\n"
                                f"Error: {refresh_err}\n"
                                f"👉 Run: `python execution/authorize_youtube.py` to re-authenticate."
                            )
                            print(f"[YouTube] ❌ Token refresh failed: {refresh_err}")
                            return False
                        
                        print(f"[YouTube] ⚠️ Token refresh attempt {attempt + 1} failed, retrying... ({refresh_err})")
                        time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
            else:
                from core.telegram_bot import send_message as _tg
                _tg(
                    f"🔑 *YouTube Re-Auth Required* — {channel_name}\n"
                    f"Token missing or invalid.\n"
                    f"👉 Run: `python execution/authorize_youtube.py` on the server."
                )
                print(f"[YouTube] ❌ ERROR: Valid token not found for {channel_name} at {token_path}")
                print(f"[YouTube] Please run: python execution/authorize_youtube.py")
                return False

        self.youtube = build("youtube", "v3", credentials=creds)
        print(f"[YouTube] Authenticated for channel: {channel_name.upper()}")
        return True

    # ----------------------------------------------------------
    def upload(self, video_path: str, thumbnail_path: str,
               title: str, description: str, tags: list[str], ctx=None) -> str | None:
        """
        Upload video and set thumbnail.
        Returns YouTube video ID on success.
        """
        if not AUTO_POST_YOUTUBE:
            print("[YouTube] Auto-post disabled. Skipping upload.")
            return None

        # Human-pattern upload timing is handled by the scheduler's existing stagger:
        # 5-25 min initial jitter + 15-45 min between channels. No in-process sleep needed.

        if not self.youtube:
            success = self.authenticate(ctx)
            if not success:
                return None

        # Format tags back into hashtags and append them to the description for SEO
        # Ensure #shorts is always present and prioritized at the front
        clean_tags = [t.lower() for t in tags]
        if "shorts" not in clean_tags:
            clean_tags.insert(0, "shorts")
        else:
            clean_tags.remove("shorts")
            clean_tags.insert(0, "shorts")
            
        # Randomize hashtag count (5-9) — always exactly 8 is a bot signal
        hashtag_count = random.randint(5, 9)
        hashtags_str = " ".join([f"#{t}" for t in clean_tags[:hashtag_count]])
        full_description = f"{description}\n\n{hashtags_str}"

        category_id = ctx.youtube_category_id if ctx is not None else YOUTUBE_CATEGORY_ID
        body = {
            "snippet": {
                "title": title[:100],
                "description": full_description[:5000],
                "tags": tags[:30],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            }
        }

        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024 * 5   # 5MB chunks
        )

        try:
            print(f"[YouTube] Uploading: {title}")
            request = self.youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    print(f"[YouTube] Upload progress: {pct}%")

            video_id = response["id"]
            print(f"[YouTube] Uploaded! https://youtube.com/shorts/{video_id}")

            # Set thumbnail
            if os.path.exists(thumbnail_path):
                self.youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path)
                ).execute()
                print("[YouTube] Thumbnail set.")

            return video_id

        except HttpError as e:
            # ── Quota exhaustion: catch explicitly so pipeline can handle retry ──
            if e.resp.status == 403 and b"quotaExceeded" in e.content:
                channel_name = ctx.channel_name if ctx is not None else os.environ.get("ACTIVE_CHANNEL", "unknown")
                print(f"[YouTube] ❌ QUOTA EXHAUSTED for project. Daily limit reached.")
                from core.telegram_bot import send_message as _tg
                _tg(
                    f"🚫 *YouTube API Quota Exhausted* — {channel_name}\n"
                    f"Daily 10,000-unit limit hit. Upload skipped for today.\n\n"
                    f"📋 *Action Required:* Apply for a quota increase at:\n"
                    f"`console.cloud.google.com → APIs → YouTube Data API v3 → Quotas → Request increase`\n\n"
                    f"The rendered video is saved and will be retried at the next scheduled run."
                )
                # Return sentinel so run_pipeline.py keeps the files for tomorrow's retry
                return "QUOTA_EXHAUSTED"
            print(f"[YouTube] Upload failed: {e}")
            return None
