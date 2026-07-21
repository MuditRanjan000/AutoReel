"""
core/channel_dna.py
Centralized channel-type detection for AutoReel.

[Phase 2 — Fix A2]
Previously, every module (video_clipper.py, script_generator.py,
video_assembler.py, trend_fetcher.py) independently checked:
    "crime" in name.lower(), "stoic" in name.lower(), etc.

This was fragile and duplicated across 5+ files. Adding a new channel
required touching every module.

This module provides a single ChannelType enum and a detect() function
that is the ONE source of truth for all channel-type branching.
"""

from enum import Enum


class ChannelType(str, Enum):
    CRIME   = "crime"
    CRICKET = "cricket"
    CULTURE = "culture"
    STOIC   = "stoic"
    TECH    = "tech"
    DEFAULT = "default"


def detect(channel_name: str = "", niche: str = "", display_name: str = "") -> ChannelType:
    """
    Detect the channel type from any combination of channel_name, niche, or display_name.
    All comparisons are case-insensitive. Returns ChannelType.DEFAULT if no match.

    Usage:
        from core.channel_dna import detect, ChannelType
        ct = detect(ctx.channel_name, ctx.niche)
        if ct == ChannelType.CRIME:
            ...

    Replace all scattered:
        "crime" in chan_name.lower() or "crime" in display_name.lower()
    with:
        detect(chan_name, niche, display_name) == ChannelType.CRIME
    """
    combined = " ".join([channel_name, niche, display_name]).lower()

    import re as _re

    def _has(keywords):
        for k in keywords:
            # Use word boundary matching for short keywords to prevent substring collisions
            # e.g. "ipl" inside "discipline", "ai" inside "trail"
            if len(k) <= 4 or " " in k:
                if _re.search(r'\b' + _re.escape(k) + r'\b', combined):
                    return True
            else:
                if k in combined:
                    return True
        return False

    if _has(["crime", "murder", "heist", "forensic", "unsolved", "truecrime"]):
        return ChannelType.CRIME
    if _has(["cricket", "ipl", "test match", "bcci"]):
        return ChannelType.CRICKET
    if _has(["culture", "dating", "travel", "taboo", "social norm", "expat"]):
        return ChannelType.CULTURE
    if _has(["stoic", "philosophy", "discipline", "mindset", "ancient", "marcus aurelius", "seneca"]):
        return ChannelType.STOIC
    if _has(["tech", "ai", "artificial intelligence", "crypto", "finance", "wealth", "money", "startup"]):
        return ChannelType.TECH
    return ChannelType.DEFAULT


def is_crime(channel_name: str = "", niche: str = "", display_name: str = "") -> bool:
    return detect(channel_name, niche, display_name) == ChannelType.CRIME


def is_cricket(channel_name: str = "", niche: str = "", display_name: str = "") -> bool:
    return detect(channel_name, niche, display_name) == ChannelType.CRICKET


def is_culture(channel_name: str = "", niche: str = "", display_name: str = "") -> bool:
    return detect(channel_name, niche, display_name) == ChannelType.CULTURE


def is_stoic(channel_name: str = "", niche: str = "", display_name: str = "") -> bool:
    return detect(channel_name, niche, display_name) == ChannelType.STOIC


def is_tech(channel_name: str = "", niche: str = "", display_name: str = "") -> bool:
    return detect(channel_name, niche, display_name) == ChannelType.TECH
