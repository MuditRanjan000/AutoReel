from config.settings import CHANNELS_DIR, LOG_DIR, OUTPUT_DIR, THUMBNAIL_DIR, DB_PATH
"""
core/channel_context.py
=======================
Thread-safe, immutable channel configuration context.

Instead of reading os.environ["ACTIVE_CHANNEL"] scattered across modules,
the pipeline instantiates ONE ChannelContext at startup and passes it
explicitly to every component that needs channel-specific settings.

Benefits:
  - No hidden global state — all config is explicit and traceable
  - Safe for parallel/threaded execution (no shared mutable env vars)
  - Testable — instantiate any channel config without env manipulation
  - Single source of truth per pipeline run

Usage:
    ctx = ChannelContext.from_env()          # reads ACTIVE_CHANNEL env var once
    ctx = ChannelContext("example_philosophy")     # direct instantiation for testing
"""

import os
import json
import re


class ChannelContext:
    """
    Immutable snapshot of a channel's configuration for one pipeline run.
    All attributes are set at construction time and never mutated.
    """

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(self, channel_name: str, channels_dir: str = None):
        """
        Load configuration from channels/<channel_name>.json.

        Args:
            channel_name: The channel slug, e.g. "example_philosophy"
            channels_dir: Override for the channels/ directory path.
                          Defaults to <project_root>/channels/
        """
        self.channel_name = channel_name.strip().lower()

        # Resolve channels directory
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if channels_dir is None:
            channels_dir = os.path.join(_root, CHANNELS_DIR)

        config_path = os.path.join(channels_dir, f"{self.channel_name}.json")

        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
        else:
            print(f"[ChannelContext] WARNING: No config found at {config_path}. Using defaults.")
            cfg = {}

        self.raw_config = cfg

        # ── Channel identity ──────────────────────────────────────────────────
        self.display_name    = cfg.get("CHANNEL_NAME",        "example_philosophy")
        self.niche           = cfg.get("NICHE",               "artificial intelligence and future tech news")
        self.tone            = cfg.get("CHANNEL_TONE",        "futuristic, fast-paced, and mind-blowing")
        self.youtube_channel_id  = cfg.get("YOUTUBE_CHANNEL_ID",  "")
        self.youtube_category_id = cfg.get("YOUTUBE_CATEGORY_ID", "28")
        self.default_tags    = cfg.get("YOUTUBE_DEFAULT_TAGS", ["shorts", "tech"])
        self.rss_feeds       = cfg.get("RSS_FEEDS",            ["https://techcrunch.com/feed/"])
        self.vocabulary      = cfg.get("VOCABULARY",           [])
        self.active          = cfg.get("active",               False)
        # Per-channel LLM skill override — e.g. example_culture uses 'culture-script-writer'
        self.skill           = cfg.get("SKILL",                "viral-script-writer")

        # ── Video / voice settings ────────────────────────────────────────────
        self.video_duration_seconds = int(cfg.get("video_duration_seconds", 55))
        self.voice_name  = cfg.get("voice",       "en-US-GuyNeural")
        self.voice_rate  = cfg.get("voice_rate",  "+12%")
        self.voice_pitch = float(cfg.get("voice_pitch", 0.0))
        self.bgm_volume  = float(cfg.get("bgm_volume", 0.05))
        self.bgm_pool    = cfg.get("bgm_pool", {})
        self.max_videos_per_day = int(cfg.get("MAX_VIDEOS_PER_DAY", 1))
        # Optional per-channel caption vertical position override (pixels from top, 0-1920).
        # e.g. set 1400 to push captions lower for channels where faces are the hook.
        self.caption_y   = cfg.get("caption_y", None)   # None = auto-detect by channel name
        # Optional per-channel caption early shift override (in seconds).
        # e.g. set 0.12 to shift captions early by 120ms.
        # If not provided, we calculate it dynamically based on voice speed and gender.
        if "caption_shift" in cfg:
            self.caption_shift = float(cfg["caption_shift"])
        else:
            rate_percent = 0.0
            if self.voice_rate:
                match = re.search(r'([+-]?\d+(?:\.\d+)?)', str(self.voice_rate))
                if match:
                    try:
                        rate_percent = float(match.group(1))
                    except ValueError:
                        pass
            
            # Base shift is 0.02s
            base_shift = 0.02
            # Faster voices need more shift; slower voices need less but NOT zero.
            # Use abs() so both directions yield a positive shift:
            #   +12% voice -> 0.02 + (12 * 0.005) = 0.08s
            #   -8%  voice -> 0.02 + ( 8 * 0.003) = 0.044s
            abs_rate = abs(rate_percent)
            if rate_percent >= 0:
                calculated_shift = base_shift + (abs_rate * 0.005)
            else:
                # Slower voice: smaller shift but never zero
                calculated_shift = base_shift + (abs_rate * 0.003)
            
            # Check if female voice (which tends to have shorter word/syllable timing windows in Whisper)
            voice_lower = self.voice_name.lower() if self.voice_name else ""
            female_indicators = ["jenny", "aria", "sonia", "emma", "ana", "christina", "michelle", "stephanie", "libby", "zuri", "natasha", "neerja"]
            is_female = any(ind in voice_lower for ind in female_indicators)
            
            if is_female:
                # Subtract 0.03 for female voice to prevent subtitles highlighting too early
                calculated_shift -= 0.03
                
            self.caption_shift = max(0.0, calculated_shift)


        # ── Filesystem paths (channel-isolated) ──────────────────────────────
        self.token_path      = os.path.join(channels_dir, f"{self.channel_name}_token.json")
        self.db_path         = DB_PATH
        self.workspace_dir   = OUTPUT_DIR
        self.thumbnail_dir   = THUMBNAIL_DIR
        self.log_dir         = LOG_DIR

    @classmethod
    def from_env(cls, default: str = "example_channel") -> "ChannelContext":
        """
        Instantiate from the ACTIVE_CHANNEL environment variable.
        This is the ONE place the env var should be read — at pipeline startup.

        Args:
            default: Fallback channel name if ACTIVE_CHANNEL is not set.
        """
        channel_name = os.environ.get("ACTIVE_CHANNEL", default).strip()
        return cls(channel_name)

    # ── Representation ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"ChannelContext(channel='{self.channel_name}', "
            f"display='{self.display_name}', "
            f"niche='{self.niche[:40]}...', "
            f"active={self.active})"
        )

    def summary(self) -> str:
        """Human-readable summary for logging."""
        return (
            f"[ChannelContext] Channel : {self.display_name} ({self.channel_name})\n"
            f"[ChannelContext] Niche   : {self.niche}\n"
            f"[ChannelContext] Voice   : {self.voice_name} @ {self.voice_rate}\n"
            f"[ChannelContext] Duration: {self.video_duration_seconds}s\n"
            f"[ChannelContext] Token   : {self.token_path}\n"
            f"[ChannelContext] DB      : {self.db_path}"
        )


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    ctx = ChannelContext("example_philosophy")
    print(ctx.summary())
    print()
    ctx2 = ChannelContext("example_sports")
    print(ctx2.summary())
