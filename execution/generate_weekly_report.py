import os
import sys
import json
from datetime import datetime, timedelta

# Ensure we can import core modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import get_connection, init_db
from core.telegram_bot import send_document

def generate_weekly_report():
    init_db()
    conn = get_connection()
    c = conn.cursor()
    
    # Time window for the last 7 days
    start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    
    # Gather Uploads Data
    c.execute("SELECT COUNT(*) FROM experiments WHERE uploaded_at >= ?", (start_date,))
    row_up = c.fetchone()
    total_uploads = row_up[0] if row_up else 0
    
    # Gather Quality Reviews Data
    c.execute("SELECT COUNT(*), SUM(upload_recommended), AVG(final_score) FROM quality_engine_reviews WHERE reviewed_at >= ?", (start_date,))
    row_qc = c.fetchone()
    
    total_reviews = row_qc[0] if row_qc and row_qc[0] else 0
    passed_reviews = row_qc[1] if row_qc and row_qc[1] else 0
    failures = total_reviews - passed_reviews
    avg_score = round(row_qc[2], 1) if row_qc and row_qc[2] else 0.0
    
    # Best and Worst Channel (based on avg score)
    c.execute("""
        SELECT channel, AVG(final_score) as avg_s, COUNT(*) as c
        FROM quality_engine_reviews 
        WHERE reviewed_at >= ? 
        GROUP BY channel 
        HAVING c > 0 
        ORDER BY avg_s DESC
    """, (start_date,))
    channel_scores = c.fetchall()
    
    best_channel = "N/A"
    worst_channel = "N/A"
    if channel_scores:
        best_channel = f"{channel_scores[0][0]} ({round(channel_scores[0][1],1)}/100)"
        worst_channel = f"{channel_scores[-1][0]} ({round(channel_scores[-1][1],1)}/100)"

    # Performance Engine Insights (Top frameworks and pacing styles)
    c.execute("SELECT parameters FROM experiments WHERE uploaded_at >= ?", (start_date,))
    rows = c.fetchall()
    
    frameworks = {}
    pacing_styles = {}
    
    for row in rows:
        try:
            params = json.loads(row[0])
            fw = params.get("narrative_framework", "Unknown")
            ps = params.get("pacing_style", "Unknown")
            frameworks[fw] = frameworks.get(fw, 0) + 1
            pacing_styles[ps] = pacing_styles.get(ps, 0) + 1
        except Exception:
            pass
            
    top_fw = max(frameworks, key=frameworks.get) if frameworks else "N/A"
    top_ps = max(pacing_styles, key=pacing_styles.get) if pacing_styles else "N/A"
    
    conn.close()
    
    # Generate Markdown Report
    report_content = f"""# 📈 AutoReel Weekly Executive Report
**Generated on**: {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Period**: {start_date} to Today

## 📊 Overview
* **Total Uploads**: {total_uploads}
* **Quality Failures Caught**: {failures} (Out of {total_reviews} reviews)
* **Empire Average Quality Score**: {avg_score}/100

## 🏆 Channel Performance
* **Best Channel**: {best_channel}
* **Worst Channel**: {worst_channel}

## 🧠 Performance Engine Insights
The AI has automatically analyzed all uploaded videos this week. The following strategies were most frequently selected by the Experiment Engine for maximum retention:

* **Top Narrative Framework**: {top_fw}
* **Top Pacing Style**: {top_ps}

---
*Generated automatically by AutoReel Operations Console.*
"""
    
    output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
    os.makedirs(output_dir, exist_ok=True)
    
    # Clean up any old weekly reports on the server first
    import glob
    for old_report in glob.glob(os.path.join(output_dir, "WEEKLY_EXECUTIVE_REPORT*.md")):
        try:
            os.remove(old_report)
        except Exception:
            pass

    # Add date to filename so Telegram treats it as a brand new file instead of caching it as (9)
    current_date = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(output_dir, f"WEEKLY_EXECUTIVE_REPORT_{current_date}.md")
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
        
    print(f"[Weekly Report] Generated at {report_path}")
    
    # Dispatch via Telegram
    send_document(report_path, "📊 Your Weekly Executive Report is ready, Sir.")

if __name__ == "__main__":
    generate_weekly_report()
