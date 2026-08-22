# Directive: YouTube OAuth Authorization & Automated Uploads

## Overview
AutoReel supports both manual video review and fully automated, scheduled uploads to YouTube using official Google OAuth2 credentials.

---

## 1. Setup Google Cloud OAuth Credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com).
2. Create a project and enable **YouTube Data API v3** and **YouTube Analytics API**.
3. Navigate to **APIs & Services** $\rightarrow$ **Credentials** $\rightarrow$ **Create Credentials** $\rightarrow$ **OAuth client ID**.
4. Set Application Type to **Desktop app**.
5. Download the client secret JSON file and save it to:
   ```
   config/youtube_client_secrets.json
   ```

---

## 2. Authorize a YouTube Channel

Run the headless OAuth helper:
```bash
ACTIVE_CHANNEL=demo_channel python execution/authorize_youtube.py
```
Follow the console prompt to visit the Google consent URL, log in with your YouTube channel account, and approve permissions.

The authorized token will be saved securely to:
```
channels/demo_channel_token.json
```

---

## 3. Enable Autonomous Uploads

In your `.env` file, set:
```env
AUTO_POST_YOUTUBE=True
```

When enabled, running the pipeline or scheduler will automatically publish approved videos, upload custom thumbnails, set titles/hashtags, and report the live YouTube URL.

---

## 4. Manual Upload Workflow

When `AUTO_POST_YOUTUBE=False` (default), AutoReel compiles completed videos to:
- **Video**: `output/videos/<run_id>_final.mp4`
- **Thumbnail**: `output/thumbnails/<run_id>_thumb.jpg`
- **Metadata**: `output/logs/<run_id>_summary.json`

Upload manually through [YouTube Studio](https://studio.youtube.com).

