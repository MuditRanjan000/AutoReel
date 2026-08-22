"""
core/experiment_tracker.py
Tracks pipeline runs and stores YouTube Analytics metrics in SQLite.
All connections are guarded with try/finally to prevent leaks on any exception.
"""

import json
import time
from datetime import datetime
from core.db import get_connection


class ExperimentTracker:

    # ── Write operations ──────────────────────────────────────
    def log_run(self, run_id: str, parameters: dict, video_id: str = None):
        """
        Log a completed pipeline run. Called right after assembly (before upload).
        parameters dict should contain all choices made for this video.
        """
        conn = get_connection()
        try:
            cursor = conn.cursor()
            
            # Check if exists
            cursor.execute("SELECT * FROM experiments WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            
            uploaded_at = datetime.utcnow().isoformat()
            param_str = json.dumps(parameters)
            
            if row:
                cursor.execute("""
                    UPDATE experiments 
                    SET video_id = ?, uploaded_at = ?, parameters = ?
                    WHERE run_id = ?
                """, (video_id, uploaded_at, param_str, run_id))
            else:
                cursor.execute("""
                    INSERT INTO experiments (run_id, video_id, uploaded_at, parameters, metrics)
                    VALUES (?, ?, ?, ?, ?)
                """, (run_id, video_id, uploaded_at, param_str, None))
                
            conn.commit()
        finally:
            conn.close()
        print(f"[Tracker] Experiment logged: {run_id} | params: {list(parameters.keys())}")

    def update_video_id(self, run_id: str, video_id: str):
        """Set the YouTube video ID after a successful upload."""
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE experiments SET video_id = ? WHERE run_id = ?", (video_id, run_id))
            conn.commit()
        finally:
            conn.close()
        print(f"[Tracker] Video ID set for {run_id}: {video_id}")

    def update_metrics(self, run_id: str, metrics: dict):
        """Store YouTube Analytics metrics for a run."""
        conn = get_connection()
        try:
            cursor = conn.cursor()
            
            metrics_str = json.dumps(metrics)
            metrics_fetched_at = datetime.utcnow().isoformat()
            
            cursor.execute("""
                UPDATE experiments 
                SET metrics = ?, metrics_fetched_at = ? 
                WHERE run_id = ?
            """, (metrics_str, metrics_fetched_at, run_id))
            conn.commit()
        finally:
            conn.close()
        
        print(f"[Tracker] Metrics saved for {run_id}: "
              f"views={metrics.get('views', 0)}, "
              f"retention={metrics.get('avg_view_percentage', 0):.1f}%")

    # ── Read operations ───────────────────────────────────────
    def _row_to_dict(self, row) -> dict:
        """Convert SQLite Row to the original JSON dictionary format for compatibility."""
        return {
            "run_id": row["run_id"],
            "video_id": row["video_id"],
            "uploaded_at": row["uploaded_at"],
            "metrics_fetched_at": row["metrics_fetched_at"],
            "parameters": json.loads(row["parameters"]) if row["parameters"] else {},
            "metrics": json.loads(row["metrics"]) if row["metrics"] else None
        }

    def get_experiments_needing_analytics(self, min_age_hours: int = 48) -> list[dict]:
        """
        Return experiments that:
          - Were uploaded to YouTube (have a video_id)
          - Are at least min_age_hours old (data has had time to stabilize)
          - Have not yet had metrics fetched
        """
        cutoff_ts = time.time() - (min_age_hours * 3600)
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM experiments WHERE video_id IS NOT NULL AND metrics IS NULL")
            rows = cursor.fetchall()
        finally:
            conn.close()
        
        result = []
        for row in rows:
            if not row["uploaded_at"]: continue
            try:
                upload_ts = datetime.fromisoformat(row["uploaded_at"]).timestamp()
                if upload_ts < cutoff_ts:
                    result.append(self._row_to_dict(row))
            except Exception:
                pass
        return result

    def get_all_with_metrics(self) -> list[dict]:
        """Return all experiments that have YouTube Analytics data."""
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM experiments WHERE metrics IS NOT NULL")
            rows = cursor.fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]

    def get_all(self) -> list[dict]:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM experiments")
            rows = cursor.fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]

    def summary(self) -> dict:
        """Quick stats about the database."""
        all_exp = self.get_all()
        with_metrics = self.get_all_with_metrics()
        return {
            "total_runs":         len(all_exp),
            "uploaded":           sum(1 for e in all_exp if e.get("video_id")),
            "with_metrics":       len(with_metrics),
            "awaiting_analytics": len(self.get_experiments_needing_analytics()),
        }
