"""
core/log.py
Structured logging for AutoReel.

[Phase 2 — Fix A3]
Previously all modules used raw print() statements.
This made cloud debugging require SSH + grep on stdout.

This module provides a get_logger() factory that:
  - On Linux (cloud): emits JSON-formatted log lines, readable by journalctl
  - On Windows (local dev): emits human-readable colored output
  - Both: writes to output/logs/autoreel.log (rotating, 10MB max, 5 backups)

Usage:
    from core.log import get_logger
    log = get_logger(__name__)
    log.info("Starting pipeline for %s", run_id)
    log.warning("[Clipper] Pexels returned 0 results for '%s'", query)
    log.error("[Validator] Gemini API exhausted after %d attempts", n)

Migration: existing print() calls can be replaced with log.info() incrementally.
The logger name (__name__) gives you free module-level filtering in journalctl:
    journalctl -u autoreel | grep core.video_clipper
"""

import logging
import logging.handlers
import os
import sys
import json as _json
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line — parseable by log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exc"] = self.formatException(record.exc_info)
        return _json.dumps(log_obj, ensure_ascii=False)


class _HumanFormatter(logging.Formatter):
    """Human-readable format for local development."""

    LEVEL_COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[0m",    # default
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[1;31m", # bold red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        color = self.LEVEL_COLORS.get(record.levelname, "")
        level = f"{color}{record.levelname:8s}{self.RESET}"
        msg = record.getMessage()
        base = f"[{ts}] {level} {record.name}: {msg}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


# ── Module-level setup (runs once at import) ───────────────────────────────

from config.settings import LOG_DIR
_LOG_DIR = LOG_DIR
os.makedirs(_LOG_DIR, exist_ok=True)

_LOG_FILE = os.path.join(_LOG_DIR, "autoreel.log")

_is_linux = sys.platform != "win32"
_root_configured = False


def _configure_root():
    global _root_configured
    if _root_configured:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # ── File handler (always JSON, rotating) ──────────────────────────
    fh = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(_JsonFormatter())
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)

    # ── Console handler ────────────────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    if _is_linux:
        # Cloud: JSON to stdout so journalctl can parse it
        ch.setFormatter(_JsonFormatter())
    else:
        # Local dev: human-readable with colors
        ch.setFormatter(_HumanFormatter())
    ch.setLevel(logging.INFO)
    root.addHandler(ch)

    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Get a module-level logger. Call once at module top-level:
        log = get_logger(__name__)
    """
    _configure_root()
    return logging.getLogger(name)
