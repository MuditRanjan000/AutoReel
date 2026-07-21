"""
core/telegram_bot.py
The King Layer communication protocol.
Handles sending messages and receiving commands from the CEO via Telegram.

Two-way commands supported:
  /status          — Show scheduler status, channel upload counts, last run
  /skip <channel>  — Skip the next upload for a specific channel
  /force <channel> — Force an immediate pipeline run for a channel
  /stats           — Show today's video count and pending A/B tests
  /help            — List available commands
"""

import requests
import threading
import time
import os
import json
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# ── Outbound ──────────────────────────────────────────────────────────────────

DEFAULT_KEYBOARD = {
    "keyboard": [
        [{"text": "📊 Status"}, {"text": "📺 Channels"}],
        [{"text": "⚙️ Limits"}, {"text": "📜 Report"}],
        [{"text": "🛑 Pause"}, {"text": "▶️ Resume"}]
    ],
    "resize_keyboard": True
}

def send_message(text: str, reply_markup: dict = None) -> bool:
    """Send a simple text message to the King."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured. All Telegram commands disabled.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    else:
        payload["reply_markup"] = DEFAULT_KEYBOARD

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 400:
            print("[Telegram] ❌ 400 Bad Request: Check if TELEGRAM_CHAT_ID is correct and you have pressed /start in the bot.")
        else:
            print(f"[Telegram] Failed to send message: {e}")
        return False
    except Exception as e:
        print(f"[Telegram] Failed to send message: {e}")
        return False


def send_document(file_path: str, caption: str = "") -> bool:
    """Send a document/file to the King."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Bot token or Chat ID not configured. Skipping document.")
        return False

    if not os.path.exists(file_path):
        print(f"[Telegram] File not found: {file_path}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"}
    
    try:
        with open(file_path, "rb") as f:
            files = {"document": f}
            response = requests.post(url, data=payload, files=files, timeout=30)
            response.raise_for_status()
            return True
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 400:
            print("[Telegram] ❌ 400 Bad Request: Check if TELEGRAM_CHAT_ID is correct and you have pressed /start in the bot.")
        else:
            print(f"[Telegram] Failed to send document: {e}")
        return False
    except Exception as e:
        print(f"[Telegram] Failed to send document: {e}")
        return False


# ── Inbound Command Processor ─────────────────────────────────────────────────

_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "bot_state.json")
_state_lock = threading.Lock()

def _load_bot_state() -> dict:
    with _state_lock:
        if not os.path.exists(_STATE_FILE):
            return {"skip_flags": [], "force_flags": []}
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Telegram] Failed to load bot state: {e}")
            return {"skip_flags": [], "force_flags": []}

def _save_bot_state(state: dict):
    with _state_lock:
        try:
            # Ensure output directory exists
            os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            print(f"[Telegram] Failed to save bot state: {e}")

def should_skip_channel(channel_name: str) -> bool:
    """Check and consume a skip flag for a channel (one-time skip)."""
    state = _load_bot_state()
    skip_flags = set(state.get("skip_flags", []))
    if channel_name in skip_flags:
        skip_flags.discard(channel_name)
        state["skip_flags"] = list(skip_flags)
        _save_bot_state(state)
        return True
    return False

def should_force_channel(channel_name: str) -> bool:
    """Check and consume a force-run flag for a channel."""
    state = _load_bot_state()
    force_flags = set(state.get("force_flags", []))
    if channel_name in force_flags:
        force_flags.discard(channel_name)
        state["force_flags"] = list(force_flags)
        _save_bot_state(state)
        return True
    return False


def _get_active_channels() -> list:
    """Read channels directory for active channels."""
    channels_dir = "channels"
    active = []
    if not os.path.exists(channels_dir):
        return active
    for filename in os.listdir(channels_dir):
        if filename.endswith(".json") and not filename.endswith("_token.json"):
            try:
                with open(os.path.join(channels_dir, filename), "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("active", False):
                        active.append(filename.replace(".json", ""))
            except Exception:
                pass
    return active



def check_cookies_validity() -> tuple[bool, str]:
    import subprocess, sys
    from core.ytdlp_utils import extend_with_cookies
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--print", "title",
        "ytsearch1:nature"
    ]
    cmd = extend_with_cookies(cmd)
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return True, "Cookies are working."
        else:
            return False, res.stderr.strip()
    except Exception as e:
        return False, str(e)


def is_global_paused() -> bool:
    state = _load_bot_state()
    return state.get("global_pause", False)

def is_channel_paused(channel_name: str) -> bool:
    state = _load_bot_state()
    return channel_name in state.get("paused_channels", [])

def set_global_pause(paused: bool):
    state = _load_bot_state()
    state["global_pause"] = paused
    _save_bot_state(state)

def set_channel_pause(channel_name: str, paused: bool):
    state = _load_bot_state()
    paused_channels = set(state.get("paused_channels", []))
    if paused:
        paused_channels.add(channel_name)
    else:
        paused_channels.discard(channel_name)
    state["paused_channels"] = list(paused_channels)
    _save_bot_state(state)

def _handle_command(text: str):
    """Parse and execute a Telegram command."""
    text = text.strip()
    
    # Map friendly keyboard buttons to raw commands
    friendly_map = {
        "📊 status": "/status",
        "📺 channels": "/channels",
        "⚙️ limits": "/limits",
        "🛑 pause": "/pause",
        "▶️ resume": "/resume",
        "📜 report": "/report",
        "status": "/status",
        "channels": "/channels",
    }
    lower_text = text.lower()
    if lower_text in friendly_map:
        text = friendly_map[lower_text]
        
    parts = text.split()
    cmd = parts[0].lower() if parts else ""

    if cmd == "/help":
        send_message(
            "👑 *AutoReel Operations Console*\n\n"
            "`/status` — System health & quality stats\n"
            "`/limits` — Quota, disk, API usage limits\n"
            "`/channels` — Per-channel status & last upload\n"
            "`/report` — Latest daily report & insights\n"
            "`/pause [channel]` — Pause operations\n"
            "`/resume [channel]` — Resume operations\n"
            "`/skip <channel|all>` — Skip next upload\n"
            "`/force <channel|all>` — Force immediate run\n"
            "`/cookies` — Update YouTube cookies\n"
            "`/check_cookies` — Verify current cookies\n"
            "`/help` — Show this menu"
        )

    elif cmd == "/status":
        channels = _get_active_channels()
        state = _load_bot_state()
        skip_list = ", ".join(state.get("skip_flags", [])) or "None"
        force_list = ", ".join(state.get("force_flags", [])) or "None"
        
        uploads_today = failures_today = 0
        pass_rate_str = "N/A"
        avg_score_str = "N/A"
        try:
            from core.db import get_connection, init_db
            from datetime import datetime, timedelta
            
            # DB runs on UTC, Python must query in UTC. Match generate_daily_report.py logic
            from datetime import timezone
            yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            
            init_db()
            conn = get_connection()
            c = conn.cursor()
            
            c.execute("SELECT COUNT(*) FROM experiments WHERE uploaded_at >= ? AND video_id IS NOT NULL", (yesterday_str,))
            row_up = c.fetchone()
            uploads_today = row_up[0] if row_up else 0
            
            c.execute("SELECT COUNT(*) FROM experiments WHERE uploaded_at >= ? AND video_id IS NULL", (yesterday_str,))
            row_crashes = c.fetchone()
            failures_today = row_crashes[0] if row_crashes else 0
            
            # Quality Engine Reviews (Programmatic Gate V2)
            c.execute("SELECT COUNT(*), SUM(final_score), SUM(upload_recommended) FROM quality_engine_reviews WHERE reviewed_at >= ?", (yesterday_str,))
            qe_row = c.fetchone()
            qe_count = qe_row[0] or 0
            qe_score_sum = qe_row[1] or 0
            qe_passed = qe_row[2] or 0
            
            # AI Reviews (Gemini)
            c.execute("SELECT COUNT(*), SUM(overall_score), SUM(upload_recommended) FROM ai_reviews WHERE reviewed_at >= ?", (yesterday_str,))
            ai_row = c.fetchone()
            ai_count = ai_row[0] or 0
            ai_score_sum = ai_row[1] or 0
            ai_passed = ai_row[2] or 0
            
            total_reviews = qe_count + ai_count
            total_passes = qe_passed + ai_passed
            
            if total_reviews > 0:
                avg_val = round((qe_score_sum + ai_score_sum) / total_reviews, 1)
                rate_val = round((total_passes / total_reviews) * 100, 1)
                pass_rate_str = f"{rate_val}%"
                avg_score_str = f"{avg_val}/100"
            
            conn.close()
        except Exception:
            pass

        sys_health = "🟢 ONLINE" if not state.get("global_pause") else "⏸️ PAUSED"
        
        send_message(
            f"📊 *System Status: {sys_health}*\n\n"
            f"*Active channels*: `{'`, `'.join(channels) if channels else 'None'}`\n"
            f"*Uploads today*: `{uploads_today}`\n"
            f"*Failures today*: `{failures_today}`\n"
            f"*Quality pass rate*: `{pass_rate_str}`\n"
            f"*Average quality score*: `{avg_score_str}`\n\n"
            f"*Force queued*: `{force_list}`\n"
            f"*Scheduled skips*: `{skip_list}`"
        )

    elif cmd == "/limits":
        import shutil
        total, used, free = shutil.disk_usage("/")
        disk_pct = round(used / total * 100, 1)
        uploads_today = 0
        try:
            from core.db import get_connection, init_db
            from datetime import datetime
            init_db()
            conn = get_connection()
            c = conn.cursor()
            from datetime import timezone
            today_str = datetime.now(timezone.utc).date().isoformat()
            c.execute("SELECT COUNT(*) FROM experiments WHERE uploaded_at >= ? AND video_id IS NOT NULL", (today_str,))
            row_up = c.fetchone()
            uploads_today = row_up[0] if row_up else 0
            conn.close()
        except Exception:
            pass
            
        send_message(
            f"⚙️ *System Limits*\n\n"
            f"*YouTube Quota Usage*: `UNLIMITED` (API limit bypassed/extended)\n"
            f"*Disk Usage*: `{disk_pct}%` ({round(used/1e9,1)}GB/{round(total/1e9,1)}GB)\n"
            f"*Upload Count Today*: `{uploads_today}`\n"
            f"*Daily Channel Limits*: `Enforced by scheduler pacing`"
        )

    elif cmd == "/channels":
        try:
            from core.db import get_connection, init_db
            init_db()
            conn = get_connection()
            c = conn.cursor()
            channels = _get_active_channels()
            state = _load_bot_state()
            paused = state.get("paused_channels", [])
            msg = "📺 *Channel Status*\n\n"
            for ch in channels:
                status = "⏸️ PAUSED" if ch in paused else "🟢 ACTIVE"
                c.execute("SELECT uploaded_at FROM experiments WHERE parameters LIKE ? AND video_id IS NOT NULL ORDER BY uploaded_at DESC LIMIT 1", (f'%\"channel_name\": \"{ch}\"%',))
                row_up = c.fetchone()
                last_up = row_up[0][:16].replace("T", " ") if row_up and row_up[0] else "Never"
                c.execute("SELECT reviewed_at FROM quality_engine_reviews WHERE channel = ? AND upload_recommended = 0 ORDER BY reviewed_at DESC LIMIT 1", (ch,))
                row_fail = c.fetchone()
                last_fail = row_fail[0][:16].replace("T", " ") if row_fail and row_fail[0] else "None"
                c.execute("SELECT AVG(final_score) FROM quality_engine_reviews WHERE channel = ?", (ch,))
                row_score = c.fetchone()
                avg_score = round(row_score[0], 1) if row_score and row_score[0] else "N/A"
                msg += f"*{ch}* ({status})\n  • Last upload: `{last_up}`\n  • Last failure: `{last_fail}`\n  • Quality avg: `{avg_score}`\n\n"
            conn.close()
            send_message(msg)
        except Exception as e:
            send_message(f"⚠️ Error: `{e}`")

    elif cmd == "/report":
        import os
        report_path = "output/WEEKLY_EXECUTIVE_REPORT.md"
        if os.path.exists(report_path):
            send_document(report_path, "📊 Latest Executive Report")
        else:
            send_message("❌ No weekly report found. It generates on Sundays.")

    elif cmd == "/cookies":
        send_message(
            "🍪 *YouTube Cookies Update*\n\n"
            "If you run AutoReel on a **Cloud Server**, cookies are managed automatically by the VNC Chromium container.\n\n"
            "If you run AutoReel **locally on your PC**, simply export cookies from your browser (via Cookie-Editor) and save them as `cookies.txt` in the root folder. yt-dlp will read them automatically!"
        )

    elif cmd in ("/check_cookies", "/verify_cookies"):
        valid, msg = check_cookies_validity()
        if valid:
            send_message("✅ *Cookies are managed natively by the server and are valid.*\n"
                         "The system uses the Chromium profile at `/home/mudit/autoReel/chrome_profile`.\n\n"
                         "Test Output:\n`" + msg + "`")
        else:
            send_message("❌ *Cookies are invalid or expired.*\n\n"
                         "The Chromium profile appears blocked. Error:\n`" + msg + "`\n\n"
                         "Please VNC into the server and complete the YouTube CAPTCHA.")

    elif cmd == "/pause":
        if len(parts) > 1:
            channel = parts[1].lower().strip()
            channels = _get_active_channels()
            channel_map = {ch.lower(): ch for ch in channels}
            if channel in channel_map:
                channel = channel_map[channel]
            if channel in channels:
                set_channel_pause(channel, True)
                send_message(f"⏸️ Channel `{channel}` is now PAUSED.")
            else:
                send_message(f"❌ Channel `{channel}` not found.")
        else:
            set_global_pause(True)
            send_message("🛑 *GLOBAL PAUSE ACTIVATED*\nAll pipelines halted.")

    elif cmd == "/resume":
        if len(parts) > 1:
            channel = parts[1].lower().strip()
            channels = _get_active_channels()
            channel_map = {ch.lower(): ch for ch in channels}
            if channel in channel_map:
                channel = channel_map[channel]
            if channel in channels:
                set_channel_pause(channel, False)
                send_message(f"▶️ Channel `{channel}` is now RESUMED.")
            else:
                send_message(f"❌ Channel `{channel}` not found.")
        else:
            set_global_pause(False)
            send_message("🟢 *GLOBAL RESUME ACTIVATED*\nAll pipelines active.")

    elif cmd == "/skip":
        if len(parts) < 2:
            channels = _get_active_channels()
            send_message(f"❌ Usage: `/skip <channel_name>`\n*Active*: `{'`, `'.join(channels)}`")
            return
        channel = parts[1].lower().strip()
        channels = _get_active_channels()
        channel_map = {ch.lower(): ch for ch in channels}
        if channel in channel_map:
            channel = channel_map[channel]
        state = _load_bot_state()
        skip_flags = set(state.get("skip_flags", []))
        if channel == "all":
            for ch in channels: skip_flags.add(ch)
            state["skip_flags"] = list(skip_flags)
            _save_bot_state(state)
            send_message("✅ Next upload for *all active channels* will be skipped.")
            return
        if channel not in channels:
            send_message(f"❌ Channel `{channel}` not found.")
            return
        skip_flags.add(channel)
        state["skip_flags"] = list(skip_flags)
        _save_bot_state(state)
        send_message(f"✅ Next upload for `{channel}` will be *skipped*.")

    elif cmd == "/force":
        if len(parts) < 2:
            channels = _get_active_channels()
            send_message(f"❌ Usage: `/force <channel_name>`\n*Active*: `{'`, `'.join(channels)}`")
            return
        channel = parts[1].lower().strip()
        channels = _get_active_channels()
        channel_map = {ch.lower(): ch for ch in channels}
        if channel in channel_map:
            channel = channel_map[channel]
        state = _load_bot_state()
        force_flags = set(state.get("force_flags", []))
        if channel == "all":
            for ch in channels: force_flags.add(ch)
            state["force_flags"] = list(force_flags)
            _save_bot_state(state)
            send_message("✅ Force-run queued for *all active channels*.")
            return
        if channel not in channels:
            send_message(f"❌ Channel `{channel}` not found.")
            return
        force_flags.add(channel)
        state["force_flags"] = list(force_flags)
        _save_bot_state(state)
        send_message(f"✅ Force-run queued for `{channel}`.")

    elif cmd == "/stats":
        send_message("`/stats` merged into `/status`. Please use `/status`.")
    else:
        send_message(f"❓ Unknown command: `{text}`\nType `/help` to see available commands.")


# ── Long-Polling Listener ─────────────────────────────────────────────────────

_last_update_id = 0

def _poll_once():
    """Fetch and process one batch of updates from Telegram."""
    global _last_update_id
    if not TELEGRAM_BOT_TOKEN:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {
            "offset": _last_update_id + 1,
            "timeout": 20,
            "allowed_updates": ["message"]
        }
        resp = requests.get(url, params=params, timeout=25)
        if resp.status_code != 200:
            return

        data = resp.json()
        for update in data.get("result", []):
            _last_update_id = max(_last_update_id, update["update_id"])
            message = update.get("message", {})
            chat_id = str(message.get("chat", {}).get("id", ""))
            text = message.get("text", "").strip()

            print(f"[Telegram Debug] Received update_id={update.get('update_id')} from chat_id={chat_id} text='{text}'")

            # Only respond to the authorized owner chat
            if chat_id != str(TELEGRAM_CHAT_ID):
                print(f"[Telegram Debug] Ignoring because {chat_id} != {TELEGRAM_CHAT_ID}")
                continue

            friendly_commands = {
                "📊 status", "📺 channels", "⚙️ limits", 
                "🛑 pause", "▶️ resume", "📜 report", 
                "status", "channels"
            }
            if text.startswith("/") or text.lower() in friendly_commands:
                print(f"[Telegram] Received command: {text}")
                _handle_command(text)
            elif text.startswith("# Netscape HTTP Cookie File"):
                send_message("❌ *Manual Cookie Upload Disabled*\n\nWe now use a server-native Chromium profile to bypass bot protection. You no longer need to paste Netscape cookies here.")

    except requests.exceptions.Timeout:
        pass  # normal for long-polling
    except Exception as e:
        print(f"[Telegram] Poll error: {e}")


def start_command_listener():
    """
    Start Telegram long-polling in a background daemon thread.
    Call this once from scheduler.py at startup.
    The thread runs silently in the background, processing commands as they arrive.
    """
    def _listener_loop():
        print("[Telegram] Command listener started. Waiting for commands...")
        while True:
            try:
                _poll_once()
            except Exception as e:
                print(f"[Telegram] Listener error: {e}")
            time.sleep(1)

    thread = threading.Thread(target=_listener_loop, daemon=True, name="TelegramListener")
    thread.start()
    return thread


if __name__ == "__main__":
    print("Testing Telegram Bot Connection...")
    success = send_message(
        "👑 *My King*, the system is online.\n\n"
        "The foundation is perfect. I await your command to build the media empire.\n\n"
        "Type /help to see available commands."
    )
    if success:
        print("Test message sent successfully. Check your Telegram!")
    else:
        print("Failed to send test message.")
