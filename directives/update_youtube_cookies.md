# Update YouTube Cookies & Bot Detection Bypass (Server-Native Chromium Method)

This SOP outlines the procedure for configuring and updating YouTube session cookies to ensure `yt-dlp` bypasses automated bot detection when downloading supplemental B-roll and BGM on headless cloud servers.

---

## 1. Overview: Server-Native Chromium Profiling

When running media scraping tools (`yt-dlp`) on cloud servers (e.g. AWS, GCP, DigitalOcean), YouTube often challenges requests with `HTTP 429: Too Many Requests` or `"Sign in to confirm you're not a bot"`. 

AutoReel solves this by supporting a persistent browser profile via the `YOUTUBE_CHROMIUM_PROFILE_PATH` setting in `.env`.

When configured, all download subsystems (Visual Clipper, Music Director, Research Agent) pass session cookies directly from the browser profile using `--cookies-from-browser`, ensuring requests match the cloud server's IP address.

---

## 2. Step-by-Step Server Setup

If YouTube requests CAPTCHA verification or video downloads fail with bot challenges on your cloud server:

### Step 1: Connect to the Server's Graphical Desktop (VNC or Web Console)
Connect to your cloud instance's GUI desktop using VNC (or an Alpine/Ubuntu headless Chromium container with browser access on `localhost:3000`).

### Step 2: Open Chromium with Your Configured Profile
Launch Chromium configured with the profile path specified in your `.env`:
```bash
# Example profile path in .env:
# YOUTUBE_CHROMIUM_PROFILE_PATH=/home/ubuntu/chrome_profile
```

### Step 3: Log in & Clear Challenges
1. Navigate to [YouTube.com](https://www.youtube.com) inside the Chromium browser.
2. Complete any CAPTCHA verification if prompted.
3. Log into a standard YouTube account (or refresh the session if already logged in).
4. Play any video for 10–15 seconds to establish valid session tokens.

### Step 4: Verify Connectivity via Telegram or CLI
From your Telegram bot, send:
```text
/check_cookies
```
Or run the diagnostic directly in the terminal:
```bash
python -c "from core.telegram_bot import check_cookies_validity; print(check_cookies_validity())"
```

If the diagnostic confirms valid cookies, the pipeline will resume automated B-roll and BGM fetching.

---

## 3. Local Desktop Execution (Windows / macOS)
If running AutoReel locally on your personal computer:
- `yt-dlp` typically does not require server-native cookie profiling because residential IP addresses are rarely challenged.
- If needed, you can export cookies from your browser via the `Cookie-Editor` extension, save the file as `cookies.txt` in the project root, and AutoReel will automatically use them as a fallback.

