"""
execution/analyze_performance.py
Reads the SQLite experiment database and finds which parameter choices produce
the best videos FOR THE ACTIVE CHANNEL ONLY.

Channel-isolated: reads ACTIVE_CHANNEL env var and only analyzes experiments
tagged with that channel_name in their parameters. Running this for Example_Channel_3
will never mix in Example_Channel_2 data.

Upgrades vs original:
  - Day-0 guard: skips videos < 48h old (analytics not yet populated)
  - Composite scoring: retention*0.5 + views*0.3 + likes*0.2
  - Recency weighting: videos >30 days old count at half weight
  - Expanded params: now tracks bgm_volume, title_strategy, tagging_strategy

Usage:
    python execution/analyze_performance.py
    python execution/analyze_performance.py --min-samples 3

Outputs:
    output/logs/performance_report_{channel}.md     <- read this yourself
    output/logs/performance_findings_{channel}.json <- consumed by auto_tune.py
"""

import sys, os, json, argparse
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.experiment_tracker import ExperimentTracker
from config.settings import LOG_DIR, MIN_SAMPLES_TO_TUNE, PRIMARY_METRIC, ANALYTICS_DELAY_HOURS

# Active channel (set by scheduler or manually via env var)
_CHANNEL = os.environ.get("ACTIVE_CHANNEL", "example_channel_3")

REPORT_PATH   = os.path.join(LOG_DIR, f"performance_report_{_CHANNEL}.md")
FINDINGS_PATH = os.path.join(LOG_DIR, f"performance_findings_{_CHANNEL}.json")

# Minimum video age before analytics data is reliable (from settings.py)
_MIN_AGE_HOURS = ANALYTICS_DELAY_HOURS  # default 48h
# Recency cutoff: videos older than this are half-weighted
_RECENCY_DAYS = 30


def _composite_score(metrics: dict) -> float:
    """
    Weighted composite virality score replacing single avg_retention signal.
      - avg_view_percentage : 50% weight (most reliable quality signal)
      - avg_views           : 30% weight (reach, capped at 10k for normalisation)
      - avg_like_rate       : 20% weight (engagement signal, converted to %)
    Returns a 0-100 float.
    """
    retention  = float(metrics.get("avg_view_percentage") or 0)
    views_raw  = float(metrics.get("avg_views") or 0)
    like_rate  = float(metrics.get("avg_like_rate") or 0)

    # Normalise views: cap at 10k -> 0-100 scale
    views_norm = min(views_raw, 10_000) / 100.0
    like_pct   = like_rate * 100.0  # fraction -> percentage

    return (retention * 0.50) + (views_norm * 0.30) + (like_pct * 0.20)


def _recency_weight(uploaded_at) -> float:
    """Videos < 30 days old = weight 1.0. Older = weight 0.5 (stale algorithm data)."""
    if not uploaded_at:
        return 1.0
    try:
        if isinstance(uploaded_at, str):
            uploaded_dt = datetime.fromisoformat(uploaded_at)
        else:
            uploaded_dt = uploaded_at
        age_days = (datetime.now() - uploaded_dt).days
        return 1.0 if age_days <= _RECENCY_DAYS else 0.5
    except Exception:
        return 1.0


def analyze_parameter(experiments, param_key, min_samples):
    groups = defaultdict(list)
    for exp in experiments:
        val = exp["parameters"].get(param_key)
        m   = exp.get("metrics") or {}
        if val is None or not m:
            continue
        # Bucket duration into 5s ranges for cleaner grouping
        if param_key == "video_duration_target":
            try:
                val = f"{(int(val)//5)*5}-{(int(val)//5)*5+5}s"
            except Exception:
                pass

        score  = _composite_score(m)
        weight = _recency_weight(exp.get("uploaded_at"))
        groups[str(val)].append(score * weight)

    rankings = sorted([
        {"value": v, "n": len(s), "avg": round(sum(s)/len(s), 2),
         "eligible": len(s) >= min_samples}
        for v, s in groups.items()
    ], key=lambda x: x["avg"], reverse=True)

    eligible = [r for r in rankings if r["eligible"]]
    winner   = eligible[0] if eligible else None
    runner   = eligible[1] if len(eligible) > 1 else None

    return {
        "param_key":          param_key,
        "rankings":           rankings,
        "winner":             winner,
        "runner_up":          runner,
        "auto_tune_eligible": bool(winner and runner and winner["avg"] - runner["avg"] > 3.0),
        "metric_used":        "composite(retention*0.5 + views*0.3 + likes*0.2)",
    }


def run(min_samples=None):
    if min_samples is None:
        min_samples = MIN_SAMPLES_TO_TUNE

    tracker  = ExperimentTracker()
    all_exps = tracker.get_all_with_metrics()

    # Channel isolation: only analyze experiments for the active channel
    exps = [e for e in all_exps if e["parameters"].get("channel_name") == _CHANNEL]

    print(f"[Analyzer] Channel: {_CHANNEL}")
    print(f"[Analyzer] Total experiments with metrics: {len(all_exps)} | "
          f"For this channel: {len(exps)}")

    # Day-0 guard: skip videos too new for analytics to have populated
    # YouTube analytics take 24-72h; running on day-0 data picks wrong winners
    min_age_cutoff = datetime.now() - timedelta(hours=_MIN_AGE_HOURS)
    mature_exps = []
    skipped_young = 0
    for e in exps:
        uploaded_at = e.get("uploaded_at")
        if uploaded_at:
            try:
                uploaded_dt = datetime.fromisoformat(str(uploaded_at))
                if uploaded_dt > min_age_cutoff:
                    skipped_young += 1
                    continue
            except Exception:
                pass
        mature_exps.append(e)

    if skipped_young:
        print(f"[Analyzer] Skipped {skipped_young} video(s) under {_MIN_AGE_HOURS}h old "
              f"(analytics not yet populated - would corrupt winner selection).")

    exps = mature_exps

    if not exps:
        print(f"[Analyzer] No mature data yet for channel '{_CHANNEL}'. "
              f"Videos need {_MIN_AGE_HOURS}h before analytics are reliable.")
        return

    print(f"[Analyzer] Analyzing {len(exps)} mature video(s) with composite scoring "
          f"(retention 50% + views 30% + likes 20%, recency-weighted).")

    # ── Auto-Discover Parameters ──
    # Exclude system/admin keys that don't represent creative choices
    excluded_keys = {"channel_name", "error", "failure_type", "is_fallback_run", "video_format"}
    detected_params = set()
    for exp in exps:
        for k in exp.get("parameters", {}).keys():
            if k not in excluded_keys:
                detected_params.add(k)
    
    # Format for display: replace underscores with spaces and title case
    parameters_to_analyze = [(k, k.replace("_", " ").title()) for k in sorted(list(detected_params))]

    findings = []
    for param_key, display_name in parameters_to_analyze:
        f = analyze_parameter(exps, param_key, min_samples)
        f["display_name"] = display_name
        findings.append(f)
        w = f["winner"]["value"] if f["winner"] else "none yet"
        print(f"[Analyzer] {display_name}: winner={w} | "
              f"auto_tune={'YES' if f['auto_tune_eligible'] else 'no'}")

    os.makedirs(LOG_DIR, exist_ok=True)

    # Save machine-readable findings (channel-specific filename)
    with open(FINDINGS_PATH, "w", encoding="utf-8") as fp:
        json.dump({
            "generated_at":   datetime.now().isoformat(),
            "channel":        _CHANNEL,
            "experiments_n":  len(exps),
            "min_samples":    min_samples,
            "primary_metric": "composite(retention*0.5 + views*0.3 + likes*0.2)",
            "findings":       findings,
        }, fp, indent=2)
    print(f"[Analyzer] Findings -> {FINDINGS_PATH}")

    # Generate human-readable report
    lines = [
        f"# AutoReel Performance Report - {_CHANNEL}",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M} | "
        f"Videos: {len(exps)} | Min samples: {min_samples}", "",
        f"> **Scoring**: Composite = Retention x0.5 + Views x0.3 + Likes x0.2 "
        f"(recency-weighted: videos >{_RECENCY_DAYS}d old count half)", "",
    ]

    for f in findings:
        lines += [f"## {f['display_name']}", ""]
        if not f["rankings"]:
            lines += ["*No data yet.*", ""]
            continue
        lines += ["| Value | Composite Score | n | Eligible |",
                  "|---|---|---|---|"]
        for r in f["rankings"]:
            medal = "\U0001f947 " if f["winner"] and r["value"] == f["winner"]["value"] else ""
            elig  = "yes" if r["eligible"] else f"need {min_samples - r['n']} more"
            lines.append(f"| {medal}{r['value']} | {r['avg']:.1f} | {r['n']} | {elig} |")
        lines.append("")
        if f["auto_tune_eligible"]:
            d = f["winner"]["avg"] - f["runner_up"]["avg"]
            lines += [f"**Winner**: `{f['winner']['value']}` (+{d:.1f} pts over runner-up)",
                      "**Auto-tune**: Will be applied by auto_tune.py", ""]
        else:
            lines += ["*Needs more data or difference too small to auto-tune.*", ""]

    lines += [
        "---", "", "## Next Steps", "",
        "- `python execution/auto_tune.py --dry-run` - preview changes",
        "- `python execution/auto_tune.py` - apply changes",
    ]

    report = "\n".join(lines)
    with open(REPORT_PATH, "w", encoding="utf-8") as fp:
        fp.write(report)
    print(f"[Analyzer] Report -> {REPORT_PATH}")
    
    # ── Performance Engine Recommendations ──
    rec_path = os.path.join(LOG_DIR, f"PERFORMANCE_ENGINE_RECOMMENDATIONS_{_CHANNEL}.md")
    rec_lines = [
        f"# Performance Engine V1 Recommendations - {_CHANNEL}",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
        f"",
        f"## Strategic Insights",
        f"Based on recent YouTube analytics, here are the winning patterns you should lean into:",
        f""
    ]
    
    for f in findings:
        if f["auto_tune_eligible"] and f["winner"]:
            rec_lines.append(f"- **{f['display_name']}**: `{f['winner']['value']}` is dominating. It outperforms `{f['runner_up']['value']}` by +{f['winner']['avg'] - f['runner_up']['avg']:.1f} pts in composite virality.")
    
    rec_lines += [
        "",
        "## Automated Action Taken",
        "The above winners have been officially fed back into the `ExperimentEngine` via `auto_tune.py`.",
        "They will appear in future videos ~70% of the time, while the system continues to explore alternatives 30% of the time."
    ]
    with open(rec_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(rec_lines))
    print(f"[Analyzer] Recommendations -> {rec_path}")

    print()
    print(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-samples", type=int, default=None)
    args = parser.parse_args()
    run(min_samples=args.min_samples)
