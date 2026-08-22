import os
import sys
import subprocess
from config.settings import YOUTUBE_CHROMIUM_PROFILE_PATH
from core.telegram_bot import send_message

def validate_all():
    """
    Runs pre-flight checks on the environment.
    If any check fails, it sends a Telegram alert and stops execution.
    """
    print("[Validator] Running pre-flight environment checks...")
    
    # 1. Check Chromium Profile format
    if YOUTUBE_CHROMIUM_PROFILE_PATH:
        if not (YOUTUBE_CHROMIUM_PROFILE_PATH.startswith("chromium:") or 
                YOUTUBE_CHROMIUM_PROFILE_PATH.startswith("chrome:") or
                YOUTUBE_CHROMIUM_PROFILE_PATH.startswith("firefox:") or
                YOUTUBE_CHROMIUM_PROFILE_PATH.startswith("edge:") or
                YOUTUBE_CHROMIUM_PROFILE_PATH.startswith("brave:")):
            err_msg = f"❌ [Canary Failed] Malformed YOUTUBE_CHROMIUM_PROFILE_PATH: {YOUTUBE_CHROMIUM_PROFILE_PATH}. Must start with 'chromium:', 'chrome:', etc."
            print(err_msg)
            send_message(err_msg)
            sys.exit(1)
            
    # 2. Check JS Runtime (Deno) for yt-dlp bot protection
    if not os.path.exists("/usr/local/bin/deno") and not os.path.exists("/usr/bin/deno"):
        # We check both standard locations
        try:
            subprocess.run(["deno", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            print("[OK] Deno JS runtime is accessible in system PATH.")
        except (FileNotFoundError, subprocess.CalledProcessError):
            err_msg = "[ERROR] CRITICAL: Deno JS runtime is missing! yt-dlp 'n challenge' bypass will fail.\n"
            err_msg += "          -> Linux Fix: curl -fsSL https://deno.land/install.sh | sh\n"
            err_msg += "          -> Mac Fix: brew install deno\n"
            err_msg += "          -> Windows Fix: iwr https://deno.land/install.ps1 -useb | iex\n"
            print(err_msg)
            send_message(err_msg)
            sys.exit(1)
            
    print("[OK] All environment checks passed successfully.")

if __name__ == "__main__":
    validate_all()
