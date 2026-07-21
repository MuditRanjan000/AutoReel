"""
scheduler.py (The CEO Agent)
Orchestrates the multi-channel empire.
Runs continuously, checks the schedule, loads the appropriate channel config,
and dispatches execution/run_pipeline.py as an isolated subprocess per channel.
"""

import schedule
import time
import os
import json
import subprocess
import sys
from datetime import datetime

# Load global settings
from config.settings import (
    get_pacing, CHANNELS_DIR, LOG_DIR, OUTPUT_DIR, THUMBNAIL_DIR,
    DB_PATH, SCHEDULER_PID_FILE, SCHEDULER_STATE_FILE
)
from core.telegram_bot import send_message, start_command_listener, should_skip_channel, should_force_channel, is_global_paused, is_channel_paused

# Persist daily upload counts so a scheduler restart doesn't reset the daily limit.
_STATS_FILE = SCHEDULER_STATE_FILE


def _load_channel_stats() -> dict:
    """Load persisted per-channel daily stats from disk."""
    try:
        if os.path.exists(_STATS_FILE):
            with open(_STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_channel_stats():
    """Write per-channel daily stats to disk after every successful upload."""
    try:
        os.makedirs(os.path.dirname(_STATS_FILE), exist_ok=True)
        with open(_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(channel_stats, f, indent=2)
    except Exception as e:
        log(f"[CEO] Failed to persist scheduler state: {e}")


# Track posts per channel: {"example_channel": {"date": "2026-05-16", "count": 1}}
# Loaded from disk so daily limits survive restarts.
channel_stats = _load_channel_stats()


def log(msg: str):
    """Standalone logger — does not depend on pipeline.py."""
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    with open(os.path.join(LOG_DIR, "scheduler.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_active_channels():
    """Read channels/ directory and return a list of active channel names (without .json)."""
    active = []
    if not os.path.exists(CHANNELS_DIR):
        return active

    for filename in os.listdir(CHANNELS_DIR):
        if filename.endswith(".json") and not filename.endswith("_token.json"):
            filepath = os.path.join(CHANNELS_DIR, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    if data.get("active", False):
                        active.append(filename.replace(".json", ""))
                except Exception as e:
                    log(f"[CEO] Error reading config {filename}: {e}")
    return active


def run_pipeline_for_channel(channel_name: str, slot_time: str = None):
    """Dispatch a separate process for the channel so settings load cleanly."""
    import random
    log(f"\n[CEO Agent] Evaluating pipeline run for: {channel_name.upper()}")

    if is_global_paused():
        log(f"[CEO] 🛑 Global pause is active. Skipping {channel_name}.")
        return

    if is_channel_paused(channel_name):
        log(f"[CEO] ⏸️ Channel {channel_name} is paused. Skipping.")
        return

    # ── Read Config First ───────────────────────────────────────────────────
    channel_config_path = os.path.join(CHANNELS_DIR, f"{channel_name}.json")
    channel_data = {}
    if os.path.exists(channel_config_path):
        try:
            with open(channel_config_path, "r", encoding="utf-8") as f:
                channel_data = json.load(f)
        except Exception as e:
            log(f"[CEO] Error reading config for {channel_name}: {e}")

    # ── Database Check for Upload Count ──────────────────────────────────────
    total_uploads = 0
    skip_exempt = True
    try:
        import sqlite3
        db_path = DB_PATH
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            log("[CEO] Enforced SQLite WAL mode on scheduler connection.")
            cursor = conn.cursor()
            
            if "upload_count_since" in channel_data:
                cursor.execute(
                    "SELECT COUNT(*) FROM experiments WHERE json_extract(parameters, '$.channel_name') = ? AND uploaded_at >= ?",
                    (channel_name, channel_data["upload_count_since"])
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM experiments WHERE json_extract(parameters, '$.channel_name') = ?",
                    (channel_name,)
                )
            
            total_uploads = cursor.fetchone()[0]
            conn.close()
            skip_exempt = total_uploads < 10  # exempt during first 10 uploads (warmup)
    except Exception as e:
        log(f"[CEO] Error reading upload count from DB: {e}")

    # ── Determine Daily Cap (Dynamic Pacing / Config override) ──────────────
    # 1. Start with dynamic limit based on upload count milestones
    if total_uploads < 5:
        dynamic_limit = 1
    elif total_uploads < 15:
        dynamic_limit = 2
    else:
        dynamic_limit = 3

    # 2. Check if config overrides limit
    max_limit = dynamic_limit
    if "MAX_VIDEOS_PER_DAY" in channel_data:
        max_limit = int(channel_data["MAX_VIDEOS_PER_DAY"])

    # 3. Enforce dynamic hard ceiling of 3 videos a day maximum
    max_limit = min(max_limit, 3)

    # ── Verify Slot-Time Compatibility (Prime Times) ──────────────────────────
    # If slot_time is None, it means it's a Telegram /force command run, so we bypass slot checking.
    if slot_time is not None:
        if max_limit == 1:
            allowed_slots = ["20:00"]
        elif max_limit == 2:
            allowed_slots = ["08:00", "20:00"]
        else:
            allowed_slots = ["04:00", "12:00", "20:00"]

        if slot_time not in allowed_slots:
            log(f"[CEO] Slot {slot_time} is not in allowed slots {allowed_slots} for {channel_name} (Limit: {max_limit}). Skipping.")
            return

        log(f"[CEO] Slot {slot_time} matches allowed slots {allowed_slots} for {channel_name}. Proceeding.")

    # Check Telegram /skip flag (set by user via Telegram command)
    if should_skip_channel(channel_name):
        log(f"[CEO] 📱 Telegram /skip command active for {channel_name}. Skipping this run.")
        send_message(f"⏭️ *{channel_name}* upload skipped as requested.")
        return

    # Auto skip-day logic (18% random skip for human pattern)
    if not skip_exempt and random.random() < 0.18:
        log(f"[CEO] 🗓️ Skip-day triggered for {channel_name} (human-pattern randomization). Will post tomorrow.")
        return

    today = datetime.now().date().isoformat()
    if channel_name not in channel_stats:
        channel_stats[channel_name] = {"date": today, "count": 0}

    if channel_stats[channel_name]["date"] != today:
        channel_stats[channel_name] = {"date": today, "count": 0}

    if channel_stats[channel_name]["count"] >= max_limit:
        log(f"[CEO] Daily limit ({max_limit}) reached for {channel_name}. Skipping.")
        return

    env = os.environ.copy()
    env["ACTIVE_CHANNEL"] = channel_name

    # Run the canonical pipeline as an isolated subprocess.
    # CRITICAL: 75-minute timeout prevents a hanging pipeline from blocking
    # the entire scheduler and all subsequent channel runs.
    PIPELINE_TIMEOUT_SECONDS = 75 * 60  # 75 minutes max per pipeline run
    import time as _time
    _run_start = _time.time()
    try:
        result = subprocess.run(
            [sys.executable, "execution/run_pipeline.py"],
            env=env,
            timeout=PIPELINE_TIMEOUT_SECONDS
        )
        elapsed = int(_time.time() - _run_start)

        if result.returncode == 0:
            channel_stats[channel_name]["count"] += 1
            _save_channel_stats()  # Persist so daily limits survive restarts
            count = channel_stats[channel_name]["count"]
            log(f"[CEO] ✅ Success! {channel_name} pipeline completed: {count}/{max_limit} today ({elapsed}s)")
            send_message(f"✅ *{channel_name}* pipeline completed ({count}/{max_limit} today, took {elapsed}s).")
        else:
            log(f"[CEO] ❌ Pipeline failed for {channel_name} (exit code {result.returncode}, {elapsed}s)")
            send_message(f"⚠️ *Alert*: Pipeline failed for *{channel_name}* (code {result.returncode}). Check scheduler.log.")

    except subprocess.TimeoutExpired:
        elapsed = int(_time.time() - _run_start)
        log(f"[CEO] ⏰ Pipeline TIMED OUT for {channel_name} after {elapsed}s — killed and continuing.")
        send_message(
            f"⏰ *Pipeline Timeout* — {channel_name}\n"
            f"Run exceeded 75 minutes and was killed automatically.\n"
            f"Check scheduler.log for the last known step."
        )
    except Exception as _sub_err:
        log(f"[CEO] ❌ Subprocess error for {channel_name}: {_sub_err}")
        send_message(f"⚠️ *Subprocess Error* — *{channel_name}*: `{str(_sub_err)[:200]}`")


def scheduled_run(slot_time: str = None):
    import random
    log(f"=== CEO Agent Waking Up for slot {slot_time or 'Manual'} at {datetime.now().strftime('%H:%M')} ===")
    active_channels = get_active_channels()

    if not active_channels:
        log("[CEO] No active channels found in config. Sleeping.")
        return

    # Stagger the first run by a random jitter offset (5-25 mins) to avoid exact-hour scheduling bot flags
    first_jitter_minutes = random.randint(5, 25)
    log(f"[CEO] Jittering first channel execution by {first_jitter_minutes} minutes for bot evasion...")
    time.sleep(first_jitter_minutes * 60)

    # Randomly shuffle channels to avoid consistent sequential pattern flags
    random.shuffle(active_channels)

    for i, channel in enumerate(active_channels):
        if i > 0:
            # Stagger subsequent channel runs by 15-45 minutes to avoid concurrent IP/upload flags
            stagger_minutes = random.randint(15, 45)
            log(f"[CEO] Staggering run for {channel.upper()} by {stagger_minutes} minutes for safety...")
            time.sleep(stagger_minutes * 60)
            
        run_pipeline_for_channel(channel, slot_time=slot_time)

    log("=== CEO Agent Finished Shift ===")


# Schedule at all potential daily posting slots (covers all dynamic milestones)
ALL_POTENTIAL_SLOTS = ["04:00", "08:00", "12:00", "20:00"]
for t in ALL_POTENTIAL_SLOTS:
    schedule.every().day.at(t).do(scheduled_run, slot_time=t)
    print(f"[CEO] Scheduled daily posting run at {t} IST")


def run_training():
    """Sunday CMO: run analytics agent for EACH active channel separately."""
    active_channels = get_active_channels()
    if not active_channels:
        log("[CEO] No active channels for Sunday analytics run.")
        return
    for channel in active_channels:
        log(f"[CEO] Running weekly CMO Analytics for: {channel.upper()}")
        env = os.environ.copy()
        env["ACTIVE_CHANNEL"] = channel
        # Run with timeouts — a hanging script must not block the whole Sunday run
        _TRAIN_TIMEOUT = 10 * 60  # 10 minutes per step
        subprocess.run([sys.executable, "execution/fetch_analytics.py"],    env=env, timeout=_TRAIN_TIMEOUT)
        subprocess.run([sys.executable, "execution/analyze_performance.py"], env=env, timeout=_TRAIN_TIMEOUT)
        subprocess.run([sys.executable, "execution/auto_tune.py"],           env=env, timeout=_TRAIN_TIMEOUT)
        subprocess.run([sys.executable, "-m", "core.agents.analytics_agent"], env=env, timeout=_TRAIN_TIMEOUT)


def run_research():
    """Saturday R&D: run research agent for EACH active channel separately."""
    active_channels = get_active_channels()
    if not active_channels:
        log("[CEO] No active channels for Saturday R&D run.")
        return
    for channel in active_channels:
        log(f"[CEO] Running weekly R&D Research for: {channel.upper()}")
        env = os.environ.copy()
        env["ACTIVE_CHANNEL"] = channel
        subprocess.run([sys.executable, "-m", "core.agents.research_agent"], env=env)


# Schedule strategy analysis every Sunday at midnight
schedule.every().sunday.at("00:00").do(run_training)
print("[CEO] Scheduled weekly Analytics/Strategy report on Sundays at 00:00")

# Schedule competitor research every Saturday at midnight
schedule.every().saturday.at("00:00").do(run_research)
print("[CEO] Scheduled weekly R&D Agent on Saturdays at 00:00")


def run_ab_tests():
    """Daily A/B title test evaluation."""
    log("[CEO] Running daily A/B title test evaluation...")
    subprocess.run([sys.executable, "execution/ab_title_test.py"], timeout=300)


def run_comment_replies():
    """Twice-daily comment reply agent."""
    log("[CEO] Running comment reply agent...")
    subprocess.run([sys.executable, "execution/reply_comments.py"], timeout=300)


# A/B title tests: run once daily, 10h after morning upload window
schedule.every().day.at("09:00").do(run_ab_tests)
print("[CEO] Scheduled daily A/B title test evaluation at 09:00 IST")

# Comment replies: twice daily — morning (after US wakes) and evening
schedule.every().day.at("10:00").do(run_comment_replies)
schedule.every().day.at("16:00").do(run_comment_replies)
print("[CEO] Scheduled comment reply agent at 10:00 and 16:00 IST")

def run_daily_owner_report():
    """Generates and sends the daily executive summary to the owner."""
    log("[CEO] Generating Daily Owner Report...")
    subprocess.run([sys.executable, "execution/generate_daily_report.py"], timeout=120)

schedule.every().day.at("08:00").do(run_daily_owner_report)
print("[CEO] Scheduled daily Owner Report at 08:00 IST")

def run_storage_janitor():
    """Twice-daily cleanup of temporary footage and final files older than 12 hours."""
    log("[CEO] Running twice-daily storage janitor to prevent disk bloat...")

    # ── yt-dlp Daily Update ──────────────────────────────────────────────────
    # Updated here (once daily) instead of per pipeline run, saving ~5-10s per video.
    try:
        import subprocess as _sp
        _sp.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "-q"],
            timeout=60, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL
        )
        log("[CEO] yt-dlp updated successfully.")
    except Exception as _ytdlp_err:
        log(f"[CEO] yt-dlp update failed (non-fatal): {_ytdlp_err}")

    import glob
    import time
    video_cutoff = time.time() - (12 * 3600)   # 12 hours for heavy video files
    log_cutoff = time.time() - (14 * 86400)    # 14 days for tiny text logs
    deleted_count = 0
    freed_bytes = 0
    
    # 1. Clean heavy media files (48h retention)
    media_dirs = [
        os.path.join(OUTPUT_DIR, "clips"),
        OUTPUT_DIR,
        THUMBNAIL_DIR
    ]
    
    for d in media_dirs:
        if not os.path.exists(d):
            continue
        for f in glob.glob(os.path.join(d, "*")):
            if os.path.isfile(f):
                try:
                    if os.path.getmtime(f) < video_cutoff:
                        freed_bytes += os.path.getsize(f)
                        os.remove(f)
                        deleted_count += 1
                except Exception:
                    pass

    # 2. Clean old logs and summaries (14-day retention)
    # WARNING: Do NOT wipe the whole directory to avoid destroying database.sqlite
    logs_dir = LOG_DIR
    if os.path.exists(logs_dir):
        import datetime
        import gzip
        import shutil
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # 2a. Rotate active logs (pipeline.log, scheduler.log)
        for log_file in ["pipeline.log", "scheduler.log"]:
            log_path = os.path.join(logs_dir, log_file)
            rotated_path = os.path.join(logs_dir, f"{log_file}.{today_str}")
            if os.path.exists(log_path):
                try:
                    os.rename(log_path, rotated_path)
                    log(f"[CEO] Rotated active log {log_file} -> {log_file}.{today_str}")
                except Exception as e:
                    log(f"[CEO] Failed to rotate {log_file}: {e}")

        # 2b. Compress old logs > 24h, delete logs > 14 days
        one_day_ago = time.time() - 86400
        for ext in ["*.log.*", "*_summary.json", "*_review.json", "*.gz"]:
            for f in glob.glob(os.path.join(logs_dir, ext)):
                if os.path.isfile(f):
                    try:
                        mtime = os.path.getmtime(f)
                        if mtime < log_cutoff:
                            freed_bytes += os.path.getsize(f)
                            os.remove(f)
                            deleted_count += 1
                            log(f"[CEO] Deleted expired log: {os.path.basename(f)}")
                        elif mtime < one_day_ago and not f.endswith(".gz"):
                            gz_path = f + ".gz"
                            with open(f, "rb") as f_in:
                                with gzip.open(gz_path, "wb") as f_out:
                                    shutil.copyfileobj(f_in, f_out)
                            os.remove(f)
                            log(f"[CEO] Compressed old log: {os.path.basename(f)}")
                    except Exception:
                        pass

    log(f"[CEO] Storage janitor finished: deleted {deleted_count} old files ({(freed_bytes/1e6):.1f} MB freed).")

# Storage Janitor: run twice daily at 03:00 and 15:00
schedule.every().day.at("03:00").do(run_storage_janitor)
schedule.every().day.at("15:00").do(run_storage_janitor)
print("[CEO] Scheduled storage janitor at 03:00 and 15:00 IST")

def run_git_backup():
    """Daily automated Git backup of the workspace codebase."""
    log("[CEO] Running automated Git backup...")
    try:
        res = subprocess.run([sys.executable, "execution/git_backup.py"], timeout=180)
        if res.returncode == 0:
            log("[CEO] Git backup completed successfully.")
        else:
            log(f"[CEO] Git backup failed with return code {res.returncode}.")
    except Exception as e:
        log(f"[CEO] Git backup failed to run: {e}")


def run_safety_monitor():
    """Runs the proactive safety and alert monitor."""
    log("[CEO] Running safety monitor...")
    subprocess.run([sys.executable, "execution/safety_monitor.py"], timeout=120)

def run_weekly_executive_report():
    """Runs the Telegram Executive Report."""
    log("[CEO] Generating Weekly Executive Report...")
    subprocess.run([sys.executable, "execution/generate_weekly_report.py"], timeout=300)

schedule.every(2).hours.do(run_safety_monitor)
print("[CEO] Scheduled Safety Monitor every 2 hours")

schedule.every().sunday.at("09:00").do(run_weekly_executive_report)
print("[CEO] Scheduled Weekly Executive Report on Sundays at 09:00 IST")

# Git Backup: run once daily at 04:00 AM (quiet hours, after storage janitor)
schedule.every().day.at("04:00").do(run_git_backup)
print("[CEO] Scheduled daily automated Git backup at 04:00 IST")


if __name__ == "__main__":
    # ── Run-lock: prevent two scheduler instances running simultaneously ──────
    # Uses a PID file instead of a socket so stale locks are detected after
    # abnormal termination (SIGKILL / power failure) — no more TIME_WAIT issues.
    import psutil
    import atexit
    _PID_FILE = SCHEDULER_PID_FILE
    os.makedirs(LOG_DIR, exist_ok=True)
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE, "r") as _pf:
                _old_pid = int(_pf.read().strip())
            if psutil.pid_exists(_old_pid):
                print(f"[CEO] FATAL: Another scheduler instance is already running (PID {_old_pid})!")
                print("[CEO] Only one instance allowed. Exiting to prevent double-uploads.")
                sys.exit(1)
            else:
                print(f"[CEO] Stale PID file found (PID {_old_pid} is dead). Cleaning up and starting.")
        except Exception as _e:
            print(f"[CEO] Could not read PID file: {_e}. Proceeding anyway.")
    with open(_PID_FILE, "w") as _pf:
        _pf.write(str(os.getpid()))
    atexit.register(lambda: os.remove(_PID_FILE) if os.path.exists(_PID_FILE) else None)
    print(f"[CEO] Run-lock acquired (PID file: {_PID_FILE}). Single instance confirmed.")

    _, max_limit = get_pacing()
    print(f"[CEO] System Online. Max {max_limit} videos/channel/day.")
    print(f"[CEO] Active channels: {', '.join(get_active_channels())}")
    print("[CEO] Press Ctrl+C to stop.\n")

    # Start two-way Telegram command listener in background thread
    start_command_listener()
    send_message(
        "\U0001f451 *AutoReel Empire Online*\n"
        "All systems running. Type /help for commands."
    )

    # 6-hour heartbeat so we know immediately if the scheduler dies silently
    def _send_heartbeat():
        active = get_active_channels()
        send_message(f"\U0001f916 Scheduler alive | Channels: {', '.join(active) or 'none'}")
    schedule.every(6).hours.do(_send_heartbeat)

    # ── Self-Healing Main Loop ────────────────────────────────────────────────
    # If the scheduler crashes due to a transient error (network blip, DB lock,
    # etc.) it automatically restarts after a backoff delay instead of dying
    # silently and halting all uploads.
    _restart_count  = 0
    _max_backoff    = 300  # cap backoff at 5 minutes

    while True:
        try:
            schedule.run_pending()

            # Check for Telegram /force commands (runs outside normal schedule)
            for channel in get_active_channels():
                if should_force_channel(channel):
                    log(f"[CEO] 📱 Telegram /force command for {channel}. Running immediately.")
                    run_pipeline_for_channel(channel)

            time.sleep(30)
            _restart_count = 0  # reset after every clean tick

        except KeyboardInterrupt:
            log("[CEO] Graceful shutdown requested (Ctrl+C). Goodbye.")
            send_message("🛑 *AutoReel Scheduler* stopped by user.")
            break

        except Exception as _e:
            _restart_count += 1
            _backoff = min(60 * _restart_count, _max_backoff)
            _err_msg = str(_e)[:300]
            log(f"[CEO] ❌ Scheduler crashed (#{_restart_count}): {_err_msg}")
            log(f"[CEO] ⏳ Auto-restarting in {_backoff}s...")
            try:
                send_message(
                    f"⚠️ *Scheduler Crashed* (#{_restart_count})\n"
                    f"Error: `{_err_msg}`\n"
                    f"Auto-restarting in {_backoff}s..."
                )
            except Exception:
                pass  # Don't let Telegram failure prevent restart
            time.sleep(_backoff)
            log("[CEO] 🔄 Restarting scheduler loop...")

