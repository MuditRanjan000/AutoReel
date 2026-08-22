import os
import sys
import shutil
import json
import time
from datetime import datetime, timedelta

# Ensure we can import core modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.telegram_bot import send_message, _load_bot_state, _save_bot_state, _get_active_channels
from core.db import get_connection, init_db

def _check_cooldown(alert_key: str, cooldown_hours: int = 24) -> bool:
    """Returns True if the alert is allowed to fire (cooldown has passed)."""
    state = _load_bot_state()
    last_fired = state.get(alert_key, 0)
    if time.time() - last_fired > cooldown_hours * 3600:
        state[alert_key] = time.time()
        _save_bot_state(state)
        return True
    return False

def check_disk_space():
    total, used, free = shutil.disk_usage("/")
    disk_pct = used / total * 100
    
    if disk_pct > 90:
        if _check_cooldown("alert_disk_90"):
            send_message(f"🚨 *CRITICAL ALERT* 🚨\n\nDisk usage has exceeded *90%* ({round(disk_pct, 1)}%). System failure imminent. Please run storage cleanup!")
    elif disk_pct > 80:
        if _check_cooldown("alert_disk_80"):
            send_message(f"⚠️ *WARNING* ⚠️\n\nDisk usage is above *80%* ({round(disk_pct, 1)}%). Consider clearing old logs and caches.")

def check_quota_and_inactivity():
    try:
        init_db()
        conn = get_connection()
        c = conn.cursor()
        
        # Quota Check (Daily)
        c.execute("SELECT COUNT(*) FROM experiments WHERE uploaded_at >= date('now')")
        row_up = c.fetchone()
        uploads_today = row_up[0] if row_up else 0
        # Quota limit bypassed - API extended
        # quota_pct = (uploads_today * 1600) / 10000 * 100
        # Warnings disabled

        # Global Inactivity Check (48 hours)
        c.execute("SELECT MAX(uploaded_at) FROM experiments")
        row_last = c.fetchone()
        if row_last and row_last[0]:
            last_upload = datetime.fromisoformat(row_last[0])
            if datetime.now() - last_upload > timedelta(hours=48):
                if _check_cooldown("alert_inactivity_48"):
                    send_message(f"🚨 *SYSTEM INACTIVE* 🚨\n\nNo successful video uploads across ANY channel in the last *48 hours*. Please investigate the logs immediately!")

        # Quality Check (Pass rate < 50% over last 10 reviews)
        c.execute("SELECT upload_recommended FROM ai_reviews ORDER BY reviewed_at DESC LIMIT 10")
        rows = c.fetchall()
        if len(rows) >= 5: # need at least a few reviews to judge
            passed = sum(1 for r in rows if r[0] == 1)
            pass_rate = (passed / len(rows)) * 100
            if pass_rate < 50:
                if _check_cooldown("alert_quality_50"):
                    send_message(f"⚠️ *QUALITY DEGRADATION* ⚠️\n\nThe Quality Engine pass rate has dropped to *{round(pass_rate, 1)}%* over the last {len(rows)} reviews. AI might be struggling with current narrative strategies.")

        # Inactive Channel Check
        channels = _get_active_channels()
        state = _load_bot_state()
        paused = state.get("paused_channels", [])
        
        for ch in channels:
            if ch in paused:
                continue # ignore paused channels
            c.execute("SELECT uploaded_at FROM experiments WHERE json_extract(parameters, '$.channel_name') = ? ORDER BY uploaded_at DESC LIMIT 1", (ch,))
            row_ch = c.fetchone()
            if row_ch and row_ch[0]:
                last_ch_upload = datetime.fromisoformat(row_ch[0])
                if datetime.now() - last_ch_upload > timedelta(hours=72): # 72 hours for a single channel
                    if _check_cooldown(f"alert_inactive_ch_{ch}"):
                        send_message(f"⚠️ *CHANNEL INACTIVE* ⚠️\n\nThe channel `{ch}` hasn't successfully uploaded anything in 72 hours.")

        conn.close()
    except Exception as e:
        print(f"Safety Monitor DB Error: {e}")

def run_all_checks():
    print("[Safety Monitor] Running checks...")
    check_disk_space()
    check_quota_and_inactivity()
    print("[Safety Monitor] Done.")

if __name__ == "__main__":
    run_all_checks()
