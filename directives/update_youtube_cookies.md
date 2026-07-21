# Update YouTube Cookies (VNC Chromium Method)

> [!WARNING]  
> **DO NOT sync this file to the public GitHub repository.**

This SOP outlines the exact procedure for updating YouTube cookies to ensure `yt-dlp` can bypass bot detection when downloading B-roll and BGM.

## The New Architecture (Global Chromium Profiling)
Previously, cookies were generated locally via an SSH tunnel and uploaded as a `cookies.txt` file via the Telegram `/cookies` command.

**This method is now obsolete.** The AutoReel platform now utilizes a **server-native VNC Chromium profile** configured via the `YOUTUBE_CHROMIUM_PROFILE_PATH` environment variable in `.env`. 

Because YouTube's anti-bot system is highly sensitive to mismatched IP addresses and headless browsers, running a real Chromium instance natively on the server provides the most robust bot-bypassing available. All agents (Music Director, Visual Clipper, R&D, and Telegram Diagnostics) now route their `yt-dlp` calls through this Chromium profile automatically.

## Step-by-Step Procedure

If the Telegram bot alerts you that cookies have expired, or if downloads begin failing with "Sign in to confirm you're not a bot", follow these steps:

### 1. VNC into the Cloud Server
Access the DigitalOcean droplet's graphical desktop environment using your preferred VNC client (or the built-in web console).

### 2. Launch Chromium
Open the Chromium browser on the server desktop. It should be configured to use the profile path defined in your `.env` (default is `/home/mudit/autoReel/chrome_profile`).

### 3. Log into YouTube and Clear CAPTCHAs
1. Navigate to [YouTube.com](https://www.youtube.com) inside the Chromium browser.
2. If prompted, complete any CAPTCHAs ("Confirm you are not a bot").
3. Log into your throwaway YouTube account (or refresh the page if already logged in).
4. Play a random video for 10-15 seconds to ensure YouTube registers the session as an active, human-driven browser.

### 4. Verify the Fix
Close Chromium and return to your Telegram bot.
Send the command:
`/check_cookies`

The bot will execute a diagnostic `yt-dlp` call using the Chromium profile and confirm if the cookies are working properly. If successful, the entire pipeline is restored.

---

*Note: The old `/cookies` command via Telegram is kept for informational purposes but no longer accepts file uploads, as `cookies.txt` is strictly a fallback for local PC execution.*
