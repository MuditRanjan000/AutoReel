import os
from config.settings import YOUTUBE_CHROMIUM_PROFILE_PATH

def extend_with_cookies(cmd: list) -> list:
    """
    Appends the appropriate cookie flags to a yt-dlp command.
    Prioritizes the server-native VNC Chromium profile if configured in .env,
    otherwise falls back to the legacy cookies.txt file if it exists.
    """
    if YOUTUBE_CHROMIUM_PROFILE_PATH:
        cmd.extend(["--cookies-from-browser", YOUTUBE_CHROMIUM_PROFILE_PATH])
    else:
        # Find cookies.txt in project root
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cookies_path = os.path.join(root_dir, "cookies.txt")
        if os.path.exists(cookies_path):
            cmd.extend(["--cookies", cookies_path])
    return cmd
