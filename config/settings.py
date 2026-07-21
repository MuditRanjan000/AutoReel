# ============================================================
#  AutoReel — Configuration
#  API keys are loaded from .env (never hardcode secrets here).
#  NOTE: Some values below are auto-managed by execution/auto_tune.py
#  based on video performance. Don't edit those manually.
# ============================================================

import os
import json
from dotenv import load_dotenv

# Load .env from project root (one directory up from config/)
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(_ENV_PATH)

# Set Google Cloud Credentials
gcp_key_path = os.path.join(os.path.dirname(__file__), "gcp-credentials.json")
if os.path.exists(gcp_key_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = gcp_key_path
else:
    print(f"[Settings] WARNING: Google Cloud credentials not found at {gcp_key_path}")

# --- Groq API (PRIMARY AI — Free, 14,400 req/day) --------------------------
# Free at https://console.groq.com — sign up with email, get key instantly.
# Powers all agents: script writing, story picking, B-roll queries, etc.
GROQ_API_KEYS = []
_groq_idx = 1
while True:
    _key = os.getenv(f"GROQ_API_KEY_{_groq_idx}")
    if not _key:
        break
    GROQ_API_KEYS.append(_key)
    _groq_idx += 1
# Filter empty, whitespace-only, or clearly malformed keys (silent 401s otherwise)
GROQ_API_KEYS = [k for k in GROQ_API_KEYS if k and k.strip() and len(k.strip()) > 10]
if not GROQ_API_KEYS:
    print("[Settings] WARNING: No valid Groq API keys found! Check .env for GROQ_API_KEY_1 etc.")
else:
    print(f"[Settings] Loaded {len(GROQ_API_KEYS)} valid Groq key(s).")
GROQ_API_KEY  = GROQ_API_KEYS[0] if GROQ_API_KEYS else ""
GROQ_MODEL    = "llama-3.1-8b-instant"   # Fast fallback model to save quota

# --- Gemini API Keys (FALLBACK — used if Groq is unavailable) ---------------
GEMINI_API_KEYS = []
_gemini_idx = 1
while True:
    _key = os.getenv(f"GEMINI_API_KEY_{_gemini_idx}")
    if not _key:
        break
    GEMINI_API_KEYS.append(_key)
    _gemini_idx += 1

# Fallback: always add system GEMINI_API_KEY to list if it exists and is not already present
if os.getenv("GEMINI_API_KEY") and os.getenv("GEMINI_API_KEY").strip() not in GEMINI_API_KEYS:
    GEMINI_API_KEYS.append(os.getenv("GEMINI_API_KEY").strip())

# Filter malformed keys (keep any key > 10 chars)
GEMINI_API_KEYS = [k.strip() for k in GEMINI_API_KEYS if k and k.strip() and len(k.strip()) > 10]

GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""
GEMINI_MODEL   = "gemini-flash-latest"

# --- ElevenLabs API Keys (Premium TTS) --------------------------
ELEVENLABS_API_KEYS = []
_el_idx = 1
while True:
    _key = os.getenv(f"ELEVENLABS_API_KEY_{_el_idx}")
    if not _key:
        break
    ELEVENLABS_API_KEYS.append(_key)
    _el_idx += 1

# Filter malformed keys
ELEVENLABS_API_KEYS = [k.strip() for k in ELEVENLABS_API_KEYS if k and k.strip() and len(k.strip()) > 10]
if not ELEVENLABS_API_KEYS:
    print("[Settings] No ElevenLabs API keys found. ElevenLabs TTS routing will be disabled.")
else:
    print(f"[Settings] Loaded {len(ELEVENLABS_API_KEYS)} valid ElevenLabs key(s).")
ELEVENLABS_API_KEY = ELEVENLABS_API_KEYS[0] if ELEVENLABS_API_KEYS else ""

# --- NVIDIA API (Optional Fallback) -----------------------------------------
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()

# --- OpenRouter API (Alternative Free Fallback) -----------------------------
OPENROUTER_API_KEYS = []
_openrouter_idx = 1
while True:
    _key = os.getenv(f"OPENROUTER_API_KEY_{_openrouter_idx}")
    if not _key:
        break
    OPENROUTER_API_KEYS.append(_key.strip())
    _openrouter_idx += 1

if os.getenv("OPENROUTER_API_KEY") and os.getenv("OPENROUTER_API_KEY") not in OPENROUTER_API_KEYS:
    OPENROUTER_API_KEYS.append(os.getenv("OPENROUTER_API_KEY").strip())

OPENROUTER_API_KEYS = [k for k in OPENROUTER_API_KEYS if k and k.strip() and len(k.strip()) > 10]

OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it:free")

# Pexels keys — dynamic scan (same pattern as Groq/Gemini, no 3-slot limit)
PEXELS_API_KEYS = []
_pex_idx = 1
while True:
    _key = os.getenv(f"PEXELS_API_KEY_{_pex_idx}")
    if not _key:
        break
    PEXELS_API_KEYS.append(_key.strip())
    _pex_idx += 1
# Fallback: single key without index
if not PEXELS_API_KEYS and os.getenv("PEXELS_API_KEY"):
    PEXELS_API_KEYS.append(os.getenv("PEXELS_API_KEY").strip())
PEXELS_API_KEYS = [k for k in PEXELS_API_KEYS if k]
PEXELS_API_KEY = PEXELS_API_KEYS[0] if PEXELS_API_KEYS else ""

# --- Pixabay API (FREE royalty-free videos/images) --------------------------
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

# --- Pollinations.ai API Key (Optional — bypasses 402 rate limits) -----------
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "")

# --- Dynamic Channel Configuration --------------------------
# The CEO Agent sets ACTIVE_CHANNEL env var before running the pipeline.
# Strip any leading/trailing spaces (common Windows CMD set issue)
if "ACTIVE_CHANNEL" in os.environ:
    os.environ["ACTIVE_CHANNEL"] = os.environ["ACTIVE_CHANNEL"].strip()

_active_channel_file = os.environ.get("ACTIVE_CHANNEL", "example_crime") + ".json"
_channel_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "channels", _active_channel_file)

if os.path.exists(_channel_path):
    with open(_channel_path, "r", encoding="utf-8-sig") as f:
        _chan = json.load(f)
else:
    # Fallback to defaults if no config exists
    _chan = {}

NICHE                = _chan.get("NICHE", "artificial intelligence and future tech news")
CHANNEL_TONE         = _chan.get("CHANNEL_TONE", "futuristic, fast-paced, and mind-blowing")
CHANNEL_NAME         = _chan.get("CHANNEL_NAME", "example_philosophy")
YOUTUBE_CHANNEL_ID   = _chan.get("YOUTUBE_CHANNEL_ID", "")
YOUTUBE_CATEGORY_ID  = _chan.get("YOUTUBE_CATEGORY_ID", "28")
YOUTUBE_DEFAULT_TAGS = _chan.get("YOUTUBE_DEFAULT_TAGS", ["shorts", "tech"])
RSS_FEEDS            = _chan.get("RSS_FEEDS", ["https://techcrunch.com/feed/"])

# --- Content Settings (per-channel overridable via channels/*.json) ---------
VIDEO_DURATION_SECONDS = int(_chan.get("video_duration_seconds", 55))  # keep under 60 for Shorts
LANGUAGE = "English"
VOCABULARY = _chan.get("VOCABULARY", [])

# --- Voiceover (per-channel, auto-tuned by execution/auto_tune.py) ----------
# TTS Engine is auto-detected from the voice name:
#   "en-US-GuyNeural"  → Microsoft Edge TTS  (FREE — no credentials needed)
#   "en-US-Journey-D"  → Google Cloud TTS    (requires config/gcp-credentials.json)
#   "21m00Tcm4TlvDq8ikWAM" → ElevenLabs     (requires ELEVENLABS_API_KEY_1 in .env)
# Default is Edge TTS so the system works for everyone out of the box.
VOICE_NAME  = _chan.get("voice",      "en-US-GuyNeural")   # ← auto_tune writes here
VOICE_RATE  = _chan.get("voice_rate", "+10%")               # ← auto_tune writes here
VOICE_PITCH = "+0Hz"

# --- YouTube ------------------------------------------------
YOUTUBE_CLIENT_SECRETS_FILE = "config/youtube_client_secrets.json"
AUTO_POST_YOUTUBE = True  # Enabled for 24/7 autonomous cloud operations
# API key used for public YouTube search queries (e.g., saturation checks)
YOUTUBE_DATA_API_KEY = os.getenv("YOUTUBE_DATA_API_KEY", "")

# --- Telegram (The King Layer) ------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Dynamic Anti-Ban Pacing & Escalation System -----------------------------
_cached_pacing = None

def get_pacing():
    """
    Lazy-loader for scheduler pacing. Prevents import-time deadlocks by only
    querying the database when needed.
    """
    global _cached_pacing
    if _cached_pacing is not None:
        return _cached_pacing

    _config_limit = _chan.get("MAX_VIDEOS_PER_DAY")
    if _config_limit is not None:
        _config_limit = int(_config_limit)

    def _get_dynamic_scheduler_pacing(channel_name: str, config_limit: int = None) -> tuple[list[str], int]:
        default_times = ["13:00"]
        default_limit = 1

        if config_limit is not None:
            if config_limit == 1: return (["20:00"], 1)
            if config_limit == 2: return (["08:00", "20:00"], 2)
            return (["04:00", "12:00", "20:00"], config_limit)

        try:
            import sqlite3
            db_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "logs")
            db_path = os.path.join(db_dir, "database.sqlite")

            if not os.path.exists(db_path):
                return (default_times, default_limit)

            # Use a short 5s timeout to prevent hanging the whole system if DB is locked
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            cursor = conn.cursor()

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='experiments'")
            if not cursor.fetchone():
                conn.close()
                return (default_times, default_limit)

            cursor.execute("SELECT parameters FROM experiments WHERE video_id IS NOT NULL")
            rows = cursor.fetchall()
            conn.close()

            channel_uploaded_count = 0
            for row in rows:
                try:
                    params = json.loads(row[0])
                    if params.get("channel_name") == channel_name:
                        channel_uploaded_count += 1
                except Exception:
                    pass

            if channel_uploaded_count < 5:
                return (["20:00"], 1)
            elif channel_uploaded_count < 15:
                return (["08:00", "20:00"], 2)
            else:
                return (["04:00", "12:00", "20:00"], 3)
        except Exception:
            return (default_times, default_limit)

    _cached_pacing = _get_dynamic_scheduler_pacing(CHANNEL_NAME, _config_limit)
    return _cached_pacing

# --- Feature Flags ------------------------------------------
FEATURE_JSON_SCHEMA = os.getenv("FEATURE_JSON_SCHEMA", "false").lower() in ("true", "1", "yes")

# --- Output & Path Configurations -----------------------------
CHANNELS_DIR  = "channels"
OUTPUT_DIR    = "output/videos"
THUMBNAIL_DIR = "output/thumbnails"
LOG_DIR       = "output/logs"
DB_PATH       = os.path.join(LOG_DIR, "database.sqlite")
SCHEDULER_PID_FILE = os.path.join(LOG_DIR, "scheduler.pid")
SCHEDULER_STATE_FILE = os.path.join(LOG_DIR, "scheduler_state.json")

# Path to the chromium profile used for native yt-dlp cookie bypassing (set in .env for production)
YOUTUBE_CHROMIUM_PROFILE_PATH = os.getenv("YOUTUBE_CHROMIUM_PROFILE_PATH", "")

MIN_DISK_SPACE_GB = float(os.getenv("MIN_DISK_SPACE_GB", "2.0"))

# Quality review score threshold for video acceptance
QUALITY_SCORE_THRESHOLD = 80

# If True, bypasses expensive Gemini LLM validation for clips
SKIP_CLIP_VALIDATION = os.getenv("SKIP_CLIP_VALIDATION", "False").lower() in ("true", "1", "yes")

# --- Self-Learning System -----------------------------------
# How many videos with the same parameter value are needed
# before auto_tune.py will act on that finding.
MIN_SAMPLES_TO_TUNE = 5

# Primary performance metric used for all comparisons.
# avg_view_percentage = what % of the video people watched on average.
# Most reliable signal for content quality (unlike views, can't be gamed).
PRIMARY_METRIC = "avg_view_percentage"

# Hours to wait after upload before fetching YouTube Analytics.
# Analytics data takes 24-72h to fully populate; 48h is the safe default.
ANALYTICS_DELAY_HOURS = 48

_cached_encoder_args = None

def get_video_encoder_args() -> list:
    """
    Auto-detects if NVIDIA NVENC hardware acceleration is supported,
    returning optimized GPU encoder arguments if available, or fallback CPU arguments.
    """
    global _cached_encoder_args
    if _cached_encoder_args is not None:
        return _cached_encoder_args

    import subprocess
    try:
        # Probe NVENC support with a small dummy file run
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=192x192:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"]
        res = subprocess.run(cmd, capture_output=True, timeout=5)
        if res.returncode == 0:
            _cached_encoder_args = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "19"]
        else:
            _cached_encoder_args = ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]
    except Exception:
        _cached_encoder_args = ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]

    return _cached_encoder_args
