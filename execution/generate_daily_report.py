import os
import sqlite3
import json
import shutil
import glob
from datetime import datetime, timedelta
from core.db import get_connection, init_db
from core.telegram_bot import send_message, send_document
from config.settings import CHANNELS_DIR, LOG_DIR

def generate_report():
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    # DB runs on UTC. Python should query in UTC.
    now_utc = datetime.utcnow()
    yesterday_utc = now_utc - timedelta(days=1)
    yesterday_str = yesterday_utc.isoformat()
    
    # For display, we use local time (IST typically)
    now_local = datetime.now()
    
    # ── DB Metrics: Uploads & Failures ──
    cursor.execute("SELECT COUNT(*) FROM experiments WHERE uploaded_at >= ?", (yesterday_str,))
    total_generated = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM experiments WHERE video_id IS NOT NULL AND uploaded_at >= ?", (yesterday_str,))
    total_uploaded = cursor.fetchone()[0] or 0
    upload_failures = total_generated - total_uploaded

    # ── DB Metrics: Recovery System ──
    cursor.execute("SELECT COUNT(*) FROM experiments WHERE run_id LIKE '%_B' AND uploaded_at >= ?", (yesterday_str,))
    fallback_attempts = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM experiments WHERE video_id IS NOT NULL AND run_id LIKE '%_B' AND uploaded_at >= ?", (yesterday_str,))
    fallback_recoveries = cursor.fetchone()[0] or 0

    # ── Channel Stats (Last 24h uploads & failures) ──
    channel_stats = {}
    cursor.execute("SELECT parameters, video_id, uploaded_at FROM experiments WHERE uploaded_at >= ?", (yesterday_str,))
    for row in cursor.fetchall():
        try:
            params = json.loads(row[0]) if row[0] else {}
            ch = params.get("channel_name", "unknown")
            is_upload = bool(row[1])
            dt_str = str(row[2])[:16].replace("T", " ") # UTC string
            
            if ch not in channel_stats:
                channel_stats[ch] = {"uploads": 0, "last_success": "N/A", "last_failure": "N/A"}
                
            if is_upload:
                channel_stats[ch]["uploads"] += 1
                if channel_stats[ch]["last_success"] == "N/A" or dt_str > channel_stats[ch]["last_success"]:
                    channel_stats[ch]["last_success"] = dt_str
            else:
                if channel_stats[ch]["last_failure"] == "N/A" or dt_str > channel_stats[ch]["last_failure"]:
                    channel_stats[ch]["last_failure"] = dt_str
        except Exception:
            pass

    # ── Unified Quality Engine Stats ──
    # 1. AI Reviews (Gemini)
    cursor.execute("SELECT COUNT(*), SUM(overall_score), SUM(upload_recommended) FROM ai_reviews WHERE reviewed_at >= ?", (yesterday_str,))
    ai_row = cursor.fetchone()
    ai_count = ai_row[0] or 0
    ai_score_sum = ai_row[1] or 0
    ai_passes = ai_row[2] or 0

    # 2. Quality Engine Reviews (Programmatic Gate V2)
    cursor.execute("SELECT COUNT(*), SUM(final_score), SUM(upload_recommended) FROM quality_engine_reviews WHERE reviewed_at >= ?", (yesterday_str,))
    qe_row = cursor.fetchone()
    qe_count = qe_row[0] or 0
    qe_score_sum = qe_row[1] or 0
    qe_passes = qe_row[2] or 0

    total_reviews = ai_count + qe_count
    total_passes = ai_passes + qe_passes
    avg_score = (ai_score_sum + qe_score_sum) / total_reviews if total_reviews > 0 else 0.0
    pass_rate = (total_passes / total_reviews * 100) if total_reviews > 0 else 100.0

    # ── Channel Health (Historical checks for inactive channels) ──
    channels_dir = CHANNELS_DIR
    if os.path.exists(channels_dir):
        for filename in os.listdir(channels_dir):
            if filename.endswith(".json") and not filename.endswith("_token.json"):
                ch_name = filename.replace(".json", "")
                if ch_name not in channel_stats:
                    cursor.execute("SELECT MAX(uploaded_at) FROM experiments WHERE parameters LIKE ? AND video_id IS NOT NULL", (f'%"{ch_name}"%',))
                    ls = cursor.fetchone()[0]
                    channel_stats[ch_name] = {"uploads": 0, "last_success": ls[:16].replace("T", " ") if ls else "N/A", "last_failure": "N/A"}
                    
    conn.close()
    
    channel_health = []
    needs_attention = []
    
    for ch, stats in channel_stats.items():
        if ch == "unknown": continue
        ls = stats["last_success"]
        lf = stats["last_failure"]
        up = stats["uploads"]
        
        status = "🟢 Healthy"
        if up == 0 and ls != "N/A" and ls < (now_utc - timedelta(days=2)).isoformat()[:16].replace("T", " "):
            status = "🔴 Inactive"
            needs_attention.append(ch)
        elif lf != "N/A" and (ls == "N/A" or lf > ls):
            status = "🟡 Failing"
            needs_attention.append(ch)
            
        # Append " UTC" for clarity on timezone
        ls_display = ls + " UTC" if ls != "N/A" else "N/A"
        lf_display = lf + " UTC" if lf != "N/A" else "N/A"
        channel_health.append(f"| **{ch}** | {ls_display} | {lf_display} | {up} | {status} |")

    # ── Infrastructure Health ──
    total, used, free = shutil.disk_usage("/")
    disk_pct = (used / total) * 100
    disk_used_gb = used / (1024**3)
    disk_total_gb = total / (1024**3)

    quota_units = total_uploaded * 1650
    # quota_pct = (quota_units / 10000) * 100 # limit bypassed

    from config.settings import GROQ_API_KEYS, GEMINI_API_KEYS
    active_groq = len(GROQ_API_KEYS)
    active_gemini = len(GEMINI_API_KEYS)

    # ── Performance Engine Insights ──
    insights = []
    for insight_file in glob.glob(os.path.join(LOG_DIR, "PERFORMANCE_ENGINE_RECOMMENDATIONS_*.md")):
        try:
            with open(insight_file, "r", encoding="utf-8") as f:
                content = f.read()
                bullets = [line.strip() for line in content.splitlines() if line.strip().startswith("- **")]
                if bullets:
                    ch_name = os.path.basename(insight_file).replace("PERFORMANCE_ENGINE_RECOMMENDATIONS_", "").replace(".md", "")
                    for b in bullets:
                        insights.append(f"* **{ch_name}:** {b.replace('- **', '').replace('**:', ' -')}")
        except Exception:
            pass

    if not insights:
        insights.append("* No new insights generated today.")

    # ── Build Markdown Report ──
    date_str = now_local.strftime("%Y-%m-%d")
    status_emoji = "🟢 ALL SYSTEMS NOMINAL" if not needs_attention else "🔴 ATTENTION NEEDED"
    att_str = ", ".join(needs_attention) if needs_attention else "None"

    report_md = f"""# 👑 AutoReel Daily Empire Report
**Date:** {date_str} (Local Time)

---

## 🚦 Executive Summary
**Status:** {status_emoji}
**Channels Needing Attention:** {att_str}

---

## 📊 Daily Production Metrics (UTC)
* **Total Generated:** {total_generated}
* **Total Uploaded:** {total_uploaded}
* **Upload Failures:** {upload_failures}

### ♻️ Recovery Metrics (Story B)
* **Fallback Attempts:** {fallback_attempts}
* **Successfully Recovered Uploads:** {fallback_recoveries}

### 🛡️ Unified Quality Gates
* **Quality Pass Rate:** {pass_rate:.1f}% ({total_passes}/{total_reviews})
* **Average Quality Score:** {avg_score:.1f} / 100
*(Includes both Programmatic Gate V2 and Gemini Visual Reviews)*

---

## 📡 Channel Health

| Channel | Last Success | Last Failure | 24h Uploads | Status |
| :--- | :--- | :--- | :--- | :--- |
"""
    report_md += "\n".join(channel_health)
    
    report_md += f"""

---

## 💾 Infrastructure Health
* **Disk Usage:** {disk_pct:.1f}% ({disk_used_gb:.1f}GB / {disk_total_gb:.1f}GB)
* **YouTube Quota Estimate:** ~{quota_units:,} units used (Limit Bypassed)
* **Active LLM Keys:** Groq ({active_groq}), Gemini ({active_gemini})

---

## 📈 Performance Engine Insights
*(Derived from recent analytics auto-tuning)*

"""
    report_md += "\n".join(insights)
    report_md += "\n\n---\n*End of Report. The next report will be generated tomorrow.*\n"

    report_dir = os.path.join(LOG_DIR, "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"DAILY_REPORT_{date_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    brief = (f"👑 *Daily Empire Report*\n\n"
             f"Status: {status_emoji}\n"
             f"Uploads: {total_uploaded} (+{fallback_recoveries} recovered)\n"
             f"Failures: {upload_failures}\n"
             f"Quality Score: {avg_score:.1f}/100\n\n"
             f"Full details attached.")
    send_document(report_path, caption=brief)
    
    print(f"[CommandCenter] Daily report generated and sent to Telegram: {report_path}")

if __name__ == "__main__":
    generate_report()
