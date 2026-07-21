import os
"""
execution/auto_tune.py
Reads performance_findings.json and applies safe, data-driven changes
to pipeline settings. Only acts on findings with enough data and a
meaningful performance difference.

Changes it can make:
  - config/settings.py: VOICE_NAME, VOICE_RATE, VIDEO_DURATION_SECONDS
  - core/experiment_engine.py: reorders pools for tone, topic, title_strategy,
    thumbnail_color, hook_style, cta_style so winners appear at top

Changes it will NOT make automatically (needs human review):
  - Skill file content
  - BGM selection
  - RSS feed list

Usage:
    python execution/auto_tune.py             # apply changes
    python execution/auto_tune.py --dry-run   # preview without writing
"""

import sys, os, re, json, argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import LOG_DIR

# ── Channel isolation ─────────────────────────────────────────────────────────
# auto_tune.py operates on ONE channel at a time (set ACTIVE_CHANNEL env var).
# This prevents a Example_Channel_2 tuning run from overwriting Example_Channel_3 settings.
_CHANNEL      = os.environ.get("ACTIVE_CHANNEL", "example_channel_1")
FINDINGS_PATH = os.path.join(LOG_DIR, f"performance_findings_{_CHANNEL}.json")
TUNE_LOG_PATH = os.path.join(LOG_DIR, f"auto_tune_history_{_CHANNEL}.json")
SETTINGS_FILE    = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "config", "settings.py")
CLIPPER_FILE     = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "core", "video_clipper.py")
CHANNEL_JSON     = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                CHANNELS_DIR, f"{_CHANNEL}.json")

# ── Statistical Significance Gate ────────────────────────────────────────────
# Prevents auto_tune from acting on noise from small sample sizes.
# Winner must have >= MIN_SAMPLES_FOR_ACTION videos AND beat runner-up by >= MIN_DELTA_THRESHOLD%.
MIN_SAMPLES_FOR_ACTION = 8    # Need at least 8 videos per variant before trusting the data
MIN_DELTA_THRESHOLD    = 3.0  # Winner must beat runner-up by ≥3% retention to be actionable


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def update_channel_json(key: str, value, dry_run: bool) -> bool:
    """
    Write a tuned parameter (voice, voice_rate, video_duration_seconds) directly
    into the channel's JSON config file so it is fully isolated per channel.
    settings.py reads these keys at startup, so the new value takes effect on
    the very next pipeline run with no global file modification.
    """
    if not os.path.exists(CHANNEL_JSON):
        print(f"  [!] Channel JSON not found: {CHANNEL_JSON}")
        return False
    with open(CHANNEL_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    old = data.get(key, "<not set>")
    if dry_run:
        print(f"  [DRY RUN] Would set channels/{_CHANNEL}.json → {key} = '{value}' (was '{old}')")
        return True
    data[key] = value
    with open(CHANNEL_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  channels/{_CHANNEL}.json → {key} = '{value}' (was '{old}')")
    return True


def update_string_setting(file_path, var_name, new_value, dry_run):
    """Replace VAR_NAME = "old" with VAR_NAME = "new" in a Python file."""
    content = _read(file_path)
    pattern = rf'^({re.escape(var_name)}\s*=\s*)"[^"]*"'
    new_line = rf'\1"{new_value}"'
    new_content, n = re.subn(pattern, new_line, content, flags=re.MULTILINE)
    if n == 0:
        print(f"  [!] Could not find {var_name} in {os.path.basename(file_path)}")
        return False
    if not dry_run:
        _write(file_path, new_content)
    return True


def update_int_setting(file_path, var_name, new_value, dry_run):
    """Replace VAR_NAME = 55 with VAR_NAME = new_value in a Python file."""
    content = _read(file_path)
    pattern = rf'^({re.escape(var_name)}\s*=\s*)\d+'
    new_line = rf'\g<1>{new_value}'
    new_content, n = re.subn(pattern, new_line, content, flags=re.MULTILINE)
    if n == 0:
        print(f"  [!] Could not find {var_name} in {os.path.basename(file_path)}")
        return False
    if not dry_run:
        _write(file_path, new_content)
    return True


def reorder_bg_pool(bg_rankings, dry_run):
    """
    Reorder HIGH_ENERGY_BACKGROUNDS in video_clipper.py so the
    best-performing queries appear first. Doesn't remove any — keeps
    exploration possible, but the random.choice call favors variety anyway.
    The reordering is just for documentation; all queries remain available.
    """
    content = _read(CLIPPER_FILE)

    # Extract current list
    match = re.search(
        r'(HIGH_ENERGY_BACKGROUNDS\s*=\s*\[)(.*?)(\])',
        content, re.DOTALL
    )
    if not match:
        print("  [!] Could not find HIGH_ENERGY_BACKGROUNDS list")
        return False

    # Sort rankings by avg retention
    sorted_queries = [r["value"] for r in sorted(bg_rankings, key=lambda x: x["avg"], reverse=True)]

    # Rebuild list block
    new_list_body = "\n" + "".join(f'    "{q}",\n' for q in sorted_queries)
    new_content = content[:match.start(2)] + new_list_body + content[match.end(2):]

    if not dry_run:
        _write(CLIPPER_FILE, new_content)
    return True


def log_tune(changes: list[dict]):
    """Append a tune event to the history log."""
    history = []
    if os.path.exists(TUNE_LOG_PATH):
        try:
            with open(TUNE_LOG_PATH, "r") as f:
                history = json.load(f)
        except Exception:
            pass
    history.append({"applied_at": datetime.now().isoformat(), "changes": changes})
    with open(TUNE_LOG_PATH, "w") as f:
        json.dump(history, f, indent=2)


def reorder_engine_pool(pool_attr: str, sorted_vals: list, engine_file: str, dry_run: bool) -> bool:
    """
    Write pool ordering to config/pool_order_{channel}.json.
    ExperimentEngine reads this file at startup to bias its exploration pools.
    
    This replaces the previous approach of regex-editing live Python source,
    which risked corrupting experiment_engine.py on malformed winner values or
    crashes mid-write.
    """
    attr_name = pool_attr.replace("self.", "")
    order_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", f"pool_order_{_CHANNEL}.json"
    )
    if dry_run:
        print(f"  [DRY RUN] Would write pool order for '{attr_name}' -> {order_file}")
        return True
    try:
        existing = {}
        if os.path.exists(order_file):
            try:
                with open(order_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing[attr_name] = sorted_vals
        with open(order_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
        print(f"  Pool order for '{attr_name}' written to {order_file}")
        return True
    except Exception as e:
        print(f"  [!] Failed to write pool order for '{attr_name}': {e}")
        return False


def load_ai_review_insights(channel: str) -> dict:
    """
    Query the ai_reviews SQLite table and return aggregated insights
    that auto_tune can use immediately — no need to wait for YouTube analytics.

    Returns dict with:
      - avg_scores_by_bgm_mood: {mood: avg_audio_score}
      - avg_scores_by_voice_rate: {rate: avg_overall_score}
      - common_critical_issues: [category names appearing most often]
      - worst_avg_dropoff_ts: timestamp string where viewers most often leave
      - review_count: int
    """
    insights = {
        "avg_scores_by_bgm_mood": {},
        "avg_scores_by_voice_rate": {},
        "common_critical_issues": [],
        "worst_avg_dropoff_ts": None,
        "review_count": 0,
    }
    try:
        from core.db import get_connection
        conn = get_connection()
        cursor = conn.cursor()

        # Check table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_reviews'")
        if not cursor.fetchone():
            conn.close()
            return insights

        # Total review count for this channel
        cursor.execute(
            "SELECT COUNT(*) FROM ai_reviews WHERE channel=? AND skipped=0",
            (channel,)
        )
        insights["review_count"] = cursor.fetchone()[0]

        if insights["review_count"] < 3:
            conn.close()
            return insights  # Not enough data yet

        # ── Correlate BGM mood with audio score ───────────────────────────────
        # Join ai_reviews with experiments via run_id
        cursor.execute("""
            SELECT e.parameters, r.audio_score, r.overall_score
            FROM ai_reviews r
            JOIN experiments e ON r.run_id = e.run_id
            WHERE r.channel = ? AND r.skipped = 0
              AND r.audio_score IS NOT NULL
        """, (channel,))
        rows = cursor.fetchall()
        bgm_mood_scores = {}
        voice_rate_scores = {}
        for row in rows:
            try:
                params = json.loads(row["parameters"] or "{}")
                mood = params.get("bgm_mood", "unknown")
                vrate = params.get("voice_rate", "unknown")
                audio_s = row["audio_score"]
                overall_s = row["overall_score"]
                if mood not in bgm_mood_scores:
                    bgm_mood_scores[mood] = []
                bgm_mood_scores[mood].append(audio_s)
                if vrate not in voice_rate_scores:
                    voice_rate_scores[vrate] = []
                voice_rate_scores[vrate].append(overall_s)
            except Exception:
                pass

        insights["avg_scores_by_bgm_mood"] = {
            mood: round(sum(scores) / len(scores), 2)
            for mood, scores in bgm_mood_scores.items() if len(scores) >= 2
        }
        insights["avg_scores_by_voice_rate"] = {
            rate: round(sum(scores) / len(scores), 2)
            for rate, scores in voice_rate_scores.items() if len(scores) >= 2
        }

        # ── Most common CRITICAL issue categories ─────────────────────────────
        cursor.execute(
            "SELECT issues_json FROM ai_reviews WHERE channel=? AND skipped=0",
            (channel,)
        )
        from collections import Counter
        issue_counter = Counter()
        for (issues_json,) in cursor.fetchall():
            try:
                issues = json.loads(issues_json or "[]")
                for issue in issues:
                    if issue.get("severity") == "CRITICAL":
                        issue_counter[issue.get("category", "unknown")] += 1
            except Exception:
                pass
        insights["common_critical_issues"] = [
            cat for cat, count in issue_counter.most_common(5) if count >= 2
        ]

        # ── Worst average dropoff timestamp ───────────────────────────────────
        cursor.execute(
            "SELECT retention_dropoff_ts FROM ai_reviews WHERE channel=? AND skipped=0 AND retention_dropoff_ts IS NOT NULL",
            (channel,)
        )
        dropoff_rows = [r[0] for r in cursor.fetchall() if r[0]]
        if dropoff_rows:
            insights["worst_avg_dropoff_ts"] = max(set(dropoff_rows), key=dropoff_rows.count)

        conn.close()
    except Exception as e:
        print(f"[AutoTune] AI review insights query failed (non-fatal): {e}")

    return insights


def run(dry_run=False):
    if not os.path.exists(FINDINGS_PATH):
        print("[AutoTune] No findings file found.")
        print("[AutoTune] Run: python execution/analyze_performance.py")
        return

    with open(FINDINGS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    findings   = data.get("findings", [])
    gen_at     = data.get("generated_at", "unknown")
    exp_n      = data.get("experiments_n", 0)
    mode       = "[DRY RUN] " if dry_run else ""

    print(f"\n[AutoTune] Channel  : {_CHANNEL}")
    print(f"[AutoTune] {mode}Findings from: {gen_at} ({exp_n} experiments)")
    print(f"[AutoTune] {'Previewing' if dry_run else 'Applying'} eligible changes...\n")

    # ── AI Review Insights (instant feedback, no YouTube wait needed) ─────────
    ai_insights = load_ai_review_insights(_CHANNEL)
    if ai_insights["review_count"] >= 3:
        print(f"[AutoTune] AI Review Insights ({ai_insights['review_count']} visual reviews):")
        if ai_insights["avg_scores_by_bgm_mood"]:
            sorted_moods = sorted(ai_insights["avg_scores_by_bgm_mood"].items(),
                                  key=lambda x: x[1], reverse=True)
            best_mood, best_score = sorted_moods[0]
            worst_mood, worst_score = sorted_moods[-1]
            print(f"  BGM Mood Audio Scores: {dict(sorted_moods)}")
            print(f"  → Best mood: '{best_mood}' (avg audio {best_score}/10) | "
                  f"Worst: '{worst_mood}' ({worst_score}/10)")
        if ai_insights["avg_scores_by_voice_rate"]:
            sorted_rates = sorted(ai_insights["avg_scores_by_voice_rate"].items(),
                                  key=lambda x: x[1], reverse=True)
            print(f"  Voice Rate Scores: {dict(sorted_rates)}")
            print(f"  → Best voice rate: '{sorted_rates[0][0]}' (avg score {sorted_rates[0][1]}/100)")
        if ai_insights["common_critical_issues"]:
            print(f"  Recurring CRITICAL issues (fix at source in skill files):")
            for cat in ai_insights["common_critical_issues"]:
                print(f"    !! {cat}")
        if ai_insights["worst_avg_dropoff_ts"]:
            print(f"  Most common viewer dropoff: {ai_insights['worst_avg_dropoff_ts']} "
                  f"— consider adding re-hook before this point")
        print()
    else:
        print(f"[AutoTune] AI Review Insights: {ai_insights['review_count']} reviews so far "
              f"(need 3+ to surface patterns)\n")

    changes_applied = []

    for f in findings:
        param     = f["param_key"]
        eligible  = f.get("auto_tune_eligible", False)
        winner    = f.get("winner")
        display   = f.get("display_name", param)

        if not eligible or not winner:
            print(f"  {display}: skip (not eligible)")
            continue

        winner_val = winner["value"]
        winner_avg = winner["avg"]
        winner_n   = winner.get("n", 0)
        runner_avg = f["runner_up"]["avg"] if f.get("runner_up") else 0
        delta      = winner_avg - runner_avg

        # ── Statistical significance gate ─────────────────────────────────────
        if winner_n < MIN_SAMPLES_FOR_ACTION:
            print(f"  {display}: skip (only {winner_n} samples for winner, need {MIN_SAMPLES_FOR_ACTION}+)")
            continue
        if delta < MIN_DELTA_THRESHOLD:
            print(f"  {display}: skip (delta {delta:.1f}% < {MIN_DELTA_THRESHOLD}% threshold — noise, not signal)")
            continue

        # ── Voice name — writes to channels/{channel}.json (per-channel isolated) ──
        if param == "voice":
            print(f"  {display}: {mode}setting voice = '{winner_val}' "
                  f"(+{delta:.1f}% retention over runner-up)")
            ok = update_channel_json("voice", winner_val, dry_run)
            if ok:
                changes_applied.append({"param": param, "new_value": winner_val,
                                        "delta": delta, "n": winner["n"]})

        # ── Voice rate — writes to channels/{channel}.json (per-channel isolated) ──
        elif param == "voice_rate":
            print(f"  {display}: {mode}setting voice_rate = '{winner_val}' "
                  f"(+{delta:.1f}%)")
            ok = update_channel_json("voice_rate", winner_val, dry_run)
            if ok:
                changes_applied.append({"param": param, "new_value": winner_val,
                                        "delta": delta, "n": winner["n"]})

        # ── Background pool reorder ──────────────────────────────
        elif param == "bg_query":
            all_rankings = f.get("rankings", [])
            eligible_rankings = [r for r in all_rankings if r["eligible"]]
            if len(eligible_rankings) >= 2:
                print(f"  {display}: {mode}reordering pool by retention performance")
                ok = reorder_bg_pool(all_rankings, dry_run)
                if ok:
                    changes_applied.append({"param": param,
                                            "action": "reordered_pool",
                                            "top": winner_val, "n": winner["n"]})
            else:
                print(f"  {display}: skip (need ≥2 eligible queries to reorder)")

        # ── Video duration — writes to channels/{channel}.json (per-channel) ──
        elif param == "video_duration_target":
            # Parse bucket like "50-55s" → use midpoint
            try:
                low = int(winner_val.split("-")[0])
                new_dur = low + 2  # midpoint of 5s bucket
                new_dur = max(30, min(58, new_dur))  # clamp to safe range
                print(f"  {display}: {mode}setting video_duration_seconds = {new_dur} "
                      f"(from bucket {winner_val}, +{delta:.1f}%)")
                ok = update_channel_json("video_duration_seconds", new_dur, dry_run)
                if ok:
                    changes_applied.append({"param": param, "new_value": new_dur,
                                            "delta": delta, "n": winner["n"]})
            except Exception as e:
                print(f"  {display}: could not parse duration bucket '{winner_val}': {e}")

        # ── Experiment Engine pool reordering ────────────────────
        # tone, topic, title_strategy, thumbnail_color, hook_style, cta_style
        elif param in ("tone", "topic", "title_strategy", "thumbnail_color",
                       "hook_style", "cta_style", "tagging_strategy", "bgm_mood",
                       "narrative_framework", "pacing_style"):
            engine_pool_map = {
                "tone":             "tones",
                "topic":            "topics",
                "title_strategy":   "title_strategies",
                "thumbnail_color":  "thumbnail_colors",
                "hook_style":       "hook_styles",
                "cta_style":        "cta_styles",
                "tagging_strategy": "tagging_strategies",
                "bgm_mood":         "bgm_moods",
                "narrative_framework": "narrative_frameworks",
                "pacing_style":     "pacing_styles",
            }
            all_rankings = f.get("rankings", [])
            pool_attr    = "self." + engine_pool_map[param]
            sorted_vals  = [r["value"] for r in
                            sorted(all_rankings, key=lambda x: x["avg"], reverse=True)]
            engine_file  = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "core", "experiment_engine.py"
            )
            print(f"  {display}: {mode}reordering ExperimentEngine pool.")
            print(f"    Winner: '{winner_val}' (+{delta:.1f}% over runner-up)")
            ok = reorder_engine_pool(pool_attr, sorted_vals, engine_file, dry_run)
            if ok:
                changes_applied.append({"param": param,
                                        "action": "pool_reordered",
                                        "top": winner_val,
                                        "n": winner["n"]})

        else:
            print(f"  {display}: auto-tune not implemented for this parameter")

    print()
    if not changes_applied:
        print(f"[AutoTune] No changes to apply (all parameters still in exploration mode).")
    elif dry_run:
        print(f"[AutoTune] DRY RUN complete — {len(changes_applied)} changes previewed.")
        print("[AutoTune] Run without --dry-run to apply them.")
    else:
        log_tune(changes_applied)
        print(f"[AutoTune] Applied {len(changes_applied)} change(s).")
        print(f"[AutoTune] History saved: {TUNE_LOG_PATH}")
        print("[AutoTune] Next run will use the updated settings automatically.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-tune pipeline settings from performance data")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
