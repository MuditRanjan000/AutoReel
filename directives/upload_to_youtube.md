# Directive: Upload Videos to YouTube

## Current Status
Auto-upload is **disabled** (`AUTO_POST_YOUTUBE = False` in `config/settings.py`).
The uploader code exists at `core/youtube_uploader.py` but OAuth credentials are not yet set up.

## To Enable Auto-Upload

### Step 1: Create OAuth Credentials
1. Go to https://console.cloud.google.com
2. Create a new project (or use existing)
3. Enable the "YouTube Data API v3"
4. Go to "Credentials" → "Create Credentials" → "OAuth 2.0 Client IDs"
5. Application type: "Desktop App"
6. Download the JSON → save as `config/youtube_client_secrets.json`

### Step 2: Authorize the App
Run `execution/authorize_youtube.py` to complete the OAuth flow.
This will open a browser window — sign in and grant access.
A `token.json` file will be created in the project root.

### Step 3: Enable in Settings
In `config/settings.py`:
```python
AUTO_POST_YOUTUBE = True
YOUTUBE_CHANNEL_ID = "your_channel_id_here"  # from youtube.com/account_advanced
```

## Manual Upload (Current Workflow)
Until OAuth is configured, the pipeline saves the final video to:
`output/videos/<run_id>_final.mp4`

Upload manually to YouTube Studio: https://studio.youtube.com

### Recommended Settings for YouTube Shorts
- Title: use `script["title"]` from `output/logs/<run_id>_summary.json`
- Description: add `#shorts` + hashtags from `script["hashtags"]`
- Visibility: Public
- Category: Science & Technology (28)
- Made for kids: No
- Thumbnail: upload `output/thumbnails/<run_id>_thumb.jpg`

## Scheduling
The scheduler (`scheduler.py`) runs the pipeline at configured times.
Best posting times (IST) are in `config/settings.py` → `POSTING_TIMES`.
Currently: `["08:00", "13:00", "20:00"]`

To run the scheduler:
```
python scheduler.py
```

## Edge Cases
- If OAuth token expires, delete `token.json` and re-run `execution/authorize_youtube.py`
- YouTube API has a daily quota of 10,000 units. Each upload costs ~1,600 units → 6 uploads/day max on free tier.
- Videos must be ≤60 seconds to qualify as YouTube Shorts.
