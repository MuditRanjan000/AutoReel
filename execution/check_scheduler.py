"""
execution/check_scheduler.py
============================
Scheduler Watchdog — Fix 6 in the Error Handling suite.

Run this as a Windows Scheduled Task every 30 minutes to ensure
the 24/7 scheduler.py process is alive. Sends a Telegram alert
if it has crashed so you can restart it immediately.

Setup (Windows Task Scheduler):
  Action: python "C:\\path\\to\\autoReel\\execution\\check_scheduler.py"
  Trigger: Every 30 minutes
  Start in: C:\\path\\to\\autoReel

Usage (manual):
  python execution/check_scheduler.py
"""

import subprocess
import sys
import os

# Prevent Windows CMD UnicodeEncodeErrors when printing emojis
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.telegram_bot import send_message


def is_scheduler_running() -> bool:
    """Check if scheduler.py is currently running as a Python process."""
    try:
        # Look for any python process with scheduler.py in its command line
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine", "/FORMAT:LIST"],
            capture_output=True, text=True
        )
        
        # Parse wmic output line by line
        for line in result.stdout.lower().splitlines():
            if "commandline=" in line:
                cmd = line.split("commandline=", 1)[1]
                # Exclude the check_scheduler.py watchdog itself
                if "scheduler.py" in cmd and "check_scheduler.py" not in cmd:
                    return True
        return False
    except Exception as e:
        print(f"[Watchdog] Process check failed: {e}")
        return False  # Assume dead if we can't check


def main():
    # If auto-post is disabled (e.g., during warmup phase), do not check or alert
    from config.settings import AUTO_POST_YOUTUBE
    if not AUTO_POST_YOUTUBE:
        print("[Watchdog] Auto-post is disabled (Warmup Phase). Skipping check.")
        return

    print("[Watchdog] Checking if scheduler.py is running...")

    if is_scheduler_running():
        print("[Watchdog] ✅ scheduler.py is alive. All good.")
        return

    # Scheduler is dead — send alert
    print("[Watchdog] ⚠️ scheduler.py is NOT running! Sending Telegram alert...")
    send_message(
        "🚨 *Scheduler Crashed!* — AutoReel\n"
        "The 24/7 `scheduler.py` process is NOT running.\n"
        "👉 SSH/RDP into the server and run:\n"
        "`python scheduler.py`\n\n"
        "No videos will be posted until it is restarted."
    )
    print("[Watchdog] ✅ Telegram alert sent.")


if __name__ == "__main__":
    main()
