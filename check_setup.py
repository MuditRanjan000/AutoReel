#!/usr/bin/env python3
"""
AutoReel Pre-Flight System Diagnostic & Environment Doctor
==========================================================
Verifies that all prerequisites, third-party binaries, environment variables,
and directories are properly configured before running the video pipeline.

Run:
    python check_setup.py
"""

import sys
import os
import shutil
import subprocess

# Force UTF-8 stdout for Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Terminal Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Windows ANSI color support
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


def print_status(status: bool, title: str, details: str = "", fix: str = ""):
    icon = f"{GREEN}[OK]{RESET}" if status else f"{RED}[MISSING]{RESET}"
    print(f"  {icon} {BOLD}{title}{RESET}")

    if details:
        print(f"      {details}")
    if not status and fix:
        print(f"      {YELLOW}↳ Fix: {fix}{RESET}")


def check_python_version() -> bool:
    v = sys.version_info
    valid = v.major == 3 and v.minor >= 10
    details = f"Detected Python {v.major}.{v.minor}.{v.micro}"
    fix = "Install Python 3.10 or higher from https://python.org"
    print_status(valid, "Python Version (>= 3.10)", details, fix)
    return valid


def check_ffmpeg() -> bool:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        print_status(
            False,
            "FFmpeg Binary in PATH",
            "FFmpeg is required for video rendering and audio mixing.",
            "Windows: choco install ffmpeg | macOS: brew install ffmpeg | Ubuntu: sudo apt install ffmpeg"
        )
        return False

    try:
        out = subprocess.check_output([ffmpeg_path, "-version"], stderr=subprocess.STDOUT, text=True)
        first_line = out.split("\n")[0]
        print_status(True, "FFmpeg Binary in PATH", f"{first_line[:60]}... ({ffmpeg_path})")
        return True
    except Exception as e:
        print_status(False, "FFmpeg Execution", str(e), "Verify FFmpeg is installed and executable.")
        return False


def check_deno() -> bool:
    deno_path = shutil.which("deno")
    if not deno_path:
        print_status(
            False,
            "Deno Binary in PATH",
            "Deno is required by yt-dlp to bypass YouTube's anti-bot protections.",
            "Windows: iwr https://deno.land/install.ps1 -useb | iex | macOS: brew install deno | Linux: curl -fsSL https://deno.land/install.sh | sh"
        )
        return False

    try:
        out = subprocess.check_output([deno_path, "--version"], stderr=subprocess.STDOUT, text=True)
        first_line = out.split("\n")[0]
        print_status(True, "Deno Binary in PATH", f"{first_line[:60]}... ({deno_path})")
        return True
    except Exception as e:
        print_status(False, "Deno Execution", str(e), "Verify Deno is installed and executable.")
        return False


def check_imagemagick() -> bool:
    magick_path = shutil.which("magick") or shutil.which("convert")
    if magick_path:
        print_status(True, "ImageMagick Binary", f"Detected ({magick_path})")
        return True
    else:
        print_status(
            True,
            "ImageMagick Binary (Optional)",
            "Not detected in PATH (fallback text/image renderer will be used).",
            "Optional: Install ImageMagick for advanced thumbnail rendering."
        )
        return True


def check_disk_space() -> bool:
    try:
        total, used, free = shutil.disk_usage(os.path.abspath(__file__))
        free_gb = free / (1024 ** 3)
        valid = free_gb >= 2.0
        details = f"{free_gb:.1f} GB free disk space available"
        fix = "Free up at least 2.0 GB of disk space to render HD video files."
        print_status(valid, "Free Disk Space (>= 2.0 GB)", details, fix)
        return valid
    except Exception:
        return True


def check_env_file() -> bool:
    root = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(root, ".env")
    if os.path.exists(env_path):
        print_status(True, ".env Configuration File", f"Found at {env_path}")
        return True
    
    # If .env doesn't exist locally, check if environment variables are already set in the OS
    has_os_keys = bool(os.getenv("GROQ_API_KEY_1") or os.getenv("GROQ_API_KEY") or os.getenv("GEMINI_API_KEY_1") or os.getenv("GEMINI_API_KEY"))
    if has_os_keys:
        print_status(True, "Environment Configuration", "Active credentials loaded from system environment (.env file optional)")
        return True

    print_status(
        False,
        ".env Configuration File",
        "No .env file found in project root.",
        "Copy .env.example to .env (e.g. cp .env.example .env) and add your API keys."
    )
    return False


def check_channels() -> bool:
    root = os.path.dirname(os.path.abspath(__file__))
    channels_dir = os.path.join(root, "channels")
    if not os.path.exists(channels_dir):
        print_status(False, "Channels Directory", "Missing channels/ directory.", "Create channels/ directory.")
        return False

    configs = [f for f in os.listdir(channels_dir) if f.endswith(".json") and not f.endswith("_token.json")]
    if configs:
        print_status(True, "Channel Configurations", f"Found {len(configs)} profile(s): {', '.join(configs)}")
        return True
    else:
        print_status(
            False,
            "Channel Configurations",
            "No channel profiles found in channels/",
            "Create a channel JSON (e.g. channels/demo_channel.json)"
        )
        return False


def check_api_keys() -> bool:
    root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, root)

    try:
        from config.settings import (
            GROQ_API_KEYS,
            GEMINI_API_KEYS,
            PEXELS_API_KEYS,
            PEXELS_API_KEY,
            PIXABAY_API_KEY,
            ELEVENLABS_API_KEYS,
        )
    except Exception as e:
        print_status(False, "Settings Import", f"Failed to load settings: {e}")
        return False

    # AI Keys
    has_ai = bool(GROQ_API_KEYS or GEMINI_API_KEYS)
    ai_details = []
    if GROQ_API_KEYS:
        ai_details.append(f"Groq: {len(GROQ_API_KEYS)} key(s)")
    if GEMINI_API_KEYS:
        ai_details.append(f"Gemini: {len(GEMINI_API_KEYS)} key(s)")

    if has_ai:
        print_status(True, "Primary AI LLM Keys", ", ".join(ai_details))
    else:
        print_status(
            False,
            "Primary AI LLM Keys",
            "No valid Groq or Gemini API keys configured.",
            "Get a free Groq key at https://console.groq.com and set GROQ_API_KEY_1 in .env"
        )

    # Footage Keys
    has_footage = bool(PEXELS_API_KEYS or PEXELS_API_KEY or PIXABAY_API_KEY)
    footage_details = []
    if PEXELS_API_KEYS:
        footage_details.append(f"Pexels: {len(PEXELS_API_KEYS)} key(s)")
    if PIXABAY_API_KEY:
        footage_details.append("Pixabay key detected")

    if has_footage:
        print_status(True, "Stock Footage Video Keys", ", ".join(footage_details))
    else:
        print_status(
            False,
            "Stock Footage Video Keys",
            "No Pexels or Pixabay API keys found.",
            "Get a free Pexels key at https://www.pexels.com/api/ and set PEXELS_API_KEY_1 in .env"
        )

    # TTS
    print_status(
        True,
        "Text-to-Speech Engine",
        "Microsoft Edge TTS active by default (100% Free, zero configuration needed)."
    )

    return has_ai and has_footage


def main():
    print(f"\n{BOLD}{CYAN}=========================================================={RESET}")
    print(f"{BOLD}{CYAN}       AutoReel v1.0 -- Pre-Flight System Diagnostic      {RESET}")
    print(f"{BOLD}{CYAN}=========================================================={RESET}\n")

    results = [
        check_python_version(),
        check_ffmpeg(),
        check_deno(),
        check_imagemagick(),
        check_disk_space(),
        check_env_file(),
        check_channels(),
        check_api_keys(),
    ]

    print(f"\n{BOLD}{CYAN}----------------------------------------------------------{RESET}")
    if all(results):
        print(f"{GREEN}{BOLD}All pre-flight checks passed! AutoReel is ready to run.{RESET}")
        print(f"\nTo generate your first video:")
        print(f"    {BOLD}python execution/run_pipeline.py --channel demo_channel{RESET}\n")
        return 0
    else:
        print(f"{RED}{BOLD}Notice: Some pre-flight checks need attention above before first run.{RESET}\n")
        return 0


if __name__ == "__main__":
    main()
