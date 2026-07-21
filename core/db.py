"""
core/db.py
SQLite database helpers for AutoReel.

All write operations use exponential-backoff retry via _retry_write()
to handle SQLite lock contention at 30-channel scale.
Stores experiments, metrics, seen stories, A/B title tests, and AI video
reviewer results safely using JSON string columns for flexibility.
"""

import sqlite3
import os
import hashlib
import re
import time
from datetime import datetime
from config.settings import LOG_DIR

DB_PATH = os.path.join(LOG_DIR, "database.sqlite")

def get_connection():
    os.makedirs(LOG_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)  # 30s timeout for multi-channel concurrency
    conn.row_factory = sqlite3.Row
    # WAL mode: allows concurrent readers + one writer without blocking between channel subprocesses
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Original experiments table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            run_id TEXT PRIMARY KEY,
            video_id TEXT,
            uploaded_at DATETIME,
            metrics_fetched_at DATETIME,
            parameters TEXT,  -- JSON string
            metrics TEXT      -- JSON string
        )
    """)

    # Seen stories — prevents duplicate content across runs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS seen_stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            story_url TEXT,
            title_hash TEXT NOT NULL,
            title TEXT,
            seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_seen_channel ON seen_stories(channel)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_seen_hash   ON seen_stories(title_hash)")

    # A/B title tests — tracks title experiments per uploaded video
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ab_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            video_id TEXT NOT NULL,
            title_a TEXT NOT NULL,
            title_b TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            switched INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ab_channel ON ab_tests(channel)")

    # Replied comments — prevents double-replying to same comment
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS replied_comments (
            comment_id TEXT PRIMARY KEY,
            channel TEXT NOT NULL,
            replied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # AI video reviewer results — feeds the self-learning loop
    # Each row = one Gemini visual review of a final MP4
    # Linked to experiments table via run_id for auto-tune correlation
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            attempt INTEGER DEFAULT 1,
            reviewed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            overall_score INTEGER,
            visual_score INTEGER,
            audio_score INTEGER,
            caption_score INTEGER,
            flow_score INTEGER,
            retention_dropoff_ts TEXT,
            watch_likelihood INTEGER,
            upload_recommended INTEGER DEFAULT 1,
            issues_json TEXT,         -- JSON array of issue objects
            fix_applied INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            skip_reason TEXT,         -- 'file_too_large'|'no_gemini_keys'|'all_keys_exhausted'
            summary TEXT,
            raw_json TEXT             -- full Gemini response for debugging
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_review_run     ON ai_reviews(run_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_review_channel ON ai_reviews(channel)")

    # Quality Engine V1 reviews
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quality_engine_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            reviewed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            hook_score INTEGER,
            visual_score INTEGER,
            broll_score INTEGER,
            retention_score INTEGER,
            story_score INTEGER,
            emotion_score INTEGER,
            subtitle_score INTEGER,
            cta_score INTEGER,
            final_score INTEGER,
            tier TEXT,
            upload_recommended INTEGER DEFAULT 1,
            rejection_reason TEXT,
            scorecard_json TEXT,
            review_source TEXT DEFAULT 'gemini'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_qe_run     ON quality_engine_reviews(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_qe_channel ON quality_engine_reviews(channel)")

    # Migrate existing databases: add review_source column if it doesn't exist
    try:
        conn.execute("ALTER TABLE quality_engine_reviews ADD COLUMN review_source TEXT DEFAULT 'gemini'")
    except sqlite3.OperationalError:
        pass  # Column already exists — no action needed

    # ── Monthly VACUUM ────────────────────────────────────────────────────────
    # WAL mode prevents SQLite's auto-vacuum. Without periodic VACUUM,
    # deleted rows leave dead pages that grow the DB file unboundedly over years.
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS _meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("SELECT value FROM _meta WHERE key = 'last_vacuum'")
        row = cursor.fetchone()
        last_vacuum_str = row[0] if row else "2000-01-01T00:00:00"
        last_vacuum = datetime.fromisoformat(last_vacuum_str)
        if (datetime.now() - last_vacuum).days >= 30:
            print("[DB] Running monthly VACUUM to reclaim space from deleted rows...")
            conn.execute("VACUUM")
            cursor.execute(
                "INSERT OR REPLACE INTO _meta (key, value) VALUES ('last_vacuum', ?)",
                (datetime.now().isoformat(),)
            )
            print("[DB] VACUUM complete.")
    except Exception as e:
        print(f"[DB] Monthly VACUUM check failed (non-fatal): {e}")

    conn.commit()
    conn.close()


# ── Write Retry Helper ───────────────────────────────────────────────────

def _retry_write(conn, sql: str, params: tuple = (), max_retries: int = 3) -> bool:
    """
    Execute a write with exponential backoff for SQLite lock contention.
    Retries up to max_retries times: 0.5s, 1s, 2s wait between attempts.
    Raises the final exception if all retries fail.
    """
    for attempt in range(max_retries):
        try:
            conn.execute(sql, params)
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                wait = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
                print(f"[DB] SQLite locked (attempt {attempt + 1}/{max_retries}), retrying in {wait:.1f}s...")
                time.sleep(wait)
            else:
                raise  # Last attempt or non-lock error — propagate
    return False


# ── Seen Stories ─────────────────────────────────────────────────────────────

def _title_hash(title: str) -> str:
    """Normalize title and return MD5 hash for dedup comparison."""
    _stop = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at"}
    words = [w for w in title.lower().strip().split() if w not in _stop]
    normalized = " ".join(words)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()

def is_story_duplicate(channel: str, title: str, url: str = None, lookback_days: int = 7) -> bool:
    """
    Returns True if this story was already produced in the last lookback_days.
    Checks both URL (exact) and title hash (fuzzy dedup).
    """
    h = _title_hash(title)
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM seen_stories
            WHERE channel = ? AND title_hash = ?
              AND seen_at >= datetime('now', ?)
            LIMIT 1
        """, (channel, h, f"-{lookback_days} days"))
        if cursor.fetchone():
            conn.close()
            print(f"[DB] Duplicate story (title hash): '{title[:60]}'")
            return True
        if url:
            cursor.execute("""
                SELECT 1 FROM seen_stories
                WHERE channel = ? AND story_url = ?
                  AND seen_at >= datetime('now', ?)
                LIMIT 1
            """, (channel, url, f"-{lookback_days} days"))
            if cursor.fetchone():
                conn.close()
                print(f"[DB] Duplicate story (URL): '{url[:80]}'")
                return True
        conn.close()
    except Exception as e:
        print(f"[DB] is_story_duplicate error: {e}")
    return False

def mark_story_seen(channel: str, title: str, url: str = None):
    """Record a story as produced so future runs can skip it."""
    h = _title_hash(title)
    try:
        conn = get_connection()
        _retry_write(conn,
            "INSERT INTO seen_stories (channel, story_url, title_hash, title) VALUES (?, ?, ?, ?)",
            (channel, url or "", h, title[:500])
        )
        conn.close()
        print(f"[DB] Marked story seen for {channel}: '{title[:60]}'")
    except sqlite3.OperationalError as e:
        print(f"[DB] mark_story_seen lock error (data may be lost): {e}")
    except Exception as e:
        print(f"[DB] mark_story_seen error: {e}")


# ── A/B Title Tests ──────────────────────────────────────────────────────────

def save_ab_test(channel: str, video_id: str, title_a: str, title_b: str):
    """Save a pending A/B title test after a video is uploaded."""
    try:
        conn = get_connection()
        _retry_write(conn,
            "INSERT INTO ab_tests (channel, video_id, title_a, title_b) VALUES (?, ?, ?, ?)",
            (channel, video_id, title_a, title_b)
        )
        conn.close()
        print(f"[DB] A/B test saved: video={video_id} | A='{title_a[:40]}' | B='{title_b[:40]}'")
    except sqlite3.OperationalError as e:
        print(f"[DB] save_ab_test lock error (A/B test may be lost): {e}")
    except Exception as e:
        print(f"[DB] save_ab_test error: {e}")

def get_pending_ab_tests(min_age_hours: float = 8.0) -> list:
    """Return active A/B tests old enough to evaluate."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM ab_tests
            WHERE active = 1
              AND created_at <= datetime('now', ?)
        """, (f"-{int(min_age_hours)} hours",))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[DB] get_pending_ab_tests error: {e}")
        return []

def resolve_ab_test(test_id: int, switched: bool):
    """Mark an A/B test as resolved."""
    try:
        conn = get_connection()
        _retry_write(conn,
            "UPDATE ab_tests SET active = 0, switched = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if switched else 0, test_id)
        )
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"[DB] resolve_ab_test lock error: {e}")
    except Exception as e:
        print(f"[DB] resolve_ab_test error: {e}")


# ── Replied Comments ─────────────────────────────────────────────────────────

def is_comment_replied(comment_id: str) -> bool:
    """Check if we already replied to this comment."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM replied_comments WHERE comment_id = ?", (comment_id,))
        result = cursor.fetchone() is not None
        conn.close()
        return result
    except Exception as e:
        print(f"[DB] is_comment_replied error: {e}")
        return False

def mark_comment_replied(comment_id: str, channel: str):
    """Record that we replied to this comment."""
    try:
        conn = get_connection()
        _retry_write(conn,
            "INSERT OR IGNORE INTO replied_comments (comment_id, channel) VALUES (?, ?)",
            (comment_id, channel)
        )
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"[DB] mark_comment_replied lock error: {e}")
    except Exception as e:
        print(f"[DB] mark_comment_replied error: {e}")


# ── Semantic Story Dedup ───────────────────────────────────────────────────

def is_story_semantically_similar(channel: str, title: str, lookback_days: int = 3) -> bool:
    """
    Catches same-event stories that escaped the exact title-hash dedup.
    Uses word-overlap ratio on the first 8 significant words.
    A 60%+ overlap = same event, skip it.
    """
    _stop = {
        "the","a","an","is","are","was","were","in","on","at","to","of",
        "and","or","for","by","it","its","has","had","have","been","this",
        "that","with","from","but","not","can","will","do","did","so","as","if",
    }

    def _sig_words(t: str) -> list:
        return [w.lower() for w in re.findall(r'[a-zA-Z]{3,}', t) if w.lower() not in _stop][:8]

    words = _sig_words(title)
    if len(words) < 3:
        return False  # Too short to compare reliably

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT title FROM seen_stories
            WHERE channel = ? AND seen_at >= datetime('now', ?)
        """, (channel, f"-{lookback_days} days"))
        rows = cursor.fetchall()
        conn.close()

        for (existing_title,) in rows:
            if not existing_title:
                continue
            existing_words = _sig_words(existing_title)
            if len(existing_words) < 3:
                continue
            overlap = len(set(words) & set(existing_words))
            ratio = overlap / max(len(words), len(existing_words))
            if ratio >= 0.6:
                print(f"[DB] Semantic duplicate (overlap={ratio:.0%}): '{title[:50]}' ≈ '{existing_title[:50]}'")
                return True
    except Exception as e:
        print(f"[DB] is_story_semantically_similar error: {e}")
    return False


if __name__ == "__main__":
    init_db()
    print(f"[DB] Database ready at {DB_PATH}")
    print("[DB] Tables: experiments, seen_stories, ab_tests, replied_comments, ai_reviews")
