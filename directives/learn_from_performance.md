# Directive: Learn From Performance

## Goal
Close the feedback loop: fetch YouTube performance data for uploaded videos,
analyze which production choices produced the best results, and automatically
tune the pipeline settings toward what's working.

## When to Run
Run the learning cycle **once per week** after you have enough uploaded videos.
The system needs ≥5 videos per parameter value to make confident decisions.
In the first 2-3 weeks, it collects data but changes nothing.

## Tools / Scripts to Use (in order)

### Step 1: Fetch Analytics
```
python execution/fetch_analytics.py
```
Pulls retention %, views, likes, watch time from YouTube Analytics API for
all uploaded videos that are ≥48 hours old and haven't been fetched yet.

Prerequisites: YouTube OAuth with `yt-analytics.readonly` scope.
If you get an auth error, delete `config/youtube_token.pickle` and run:
```
python execution/authorize_youtube.py
```
(The new OAuth flow includes the analytics scope automatically.)

### Step 2: Analyze
```
ACTIVE_CHANNEL=example_channel_3 python execution/analyze_performance.py
```
Groups videos by their production parameters and computes average retention
per group, isolated by the active channel. Generates:
- `output/logs/performance_report_{channel}.md` — human-readable findings
- `output/logs/performance_findings_{channel}.json` — machine-readable, used by auto_tune

Read the report. Do the findings make intuitive sense?

### Step 3: Preview Changes
```
ACTIVE_CHANNEL=example_channel_3 python execution/auto_tune.py --dry-run
```
Shows what settings would be changed without actually changing anything.
Review this before applying.

### Step 4: Apply Changes
```
ACTIVE_CHANNEL=example_channel_3 python execution/auto_tune.py
```
Applies eligible changes directly to the channel's JSON configuration file (`channels/{channel}.json`) and background gameplay pool (`core/video_clipper.py`).
Logs every change to `output/logs/auto_tune_history_{channel}.json`.

## What the System Tracks (per video)

| Parameter | What it is | Auto-tuned? |
|---|---|---|
| `voice` | Which edge-tts voice was used | Yes |
| `voice_rate` | Speed setting (+15%, +20%, etc.) | Yes |
| `bg_query` | Which background gameplay query was used | Yes (reorders pool) |
| `video_duration_target` | Target duration in seconds | Yes |
| `overlay_count` | How many PIP clips appeared | No (informational) |
| `topic_emotion` | Emotion type from story-picker | No (informational) |
| `hook_word_count` | Length of the opening hook sentence | No (informational) |

## Auto-Tune Rules (hard limits)

- **Minimum samples**: 5 videos per parameter value. No changes before that.
- **Minimum delta**: >3% retention difference between winner and runner-up.
  Smaller differences are noise, not signal.
- **Channel Isolated changes**: voice, rate, duration are written to `channels/{channel}.json` to avoid contaminating other channels. B-roll pool is updated in `core/video_clipper.py`. Skill file content and RSS feeds are NEVER auto-changed.
- **Change log**: every auto_tune run appends to `auto_tune_history_{channel}.json`.
  You can see exactly what changed and why.

## Primary Metric: avg_view_percentage

This is the % of the video that people watch on average.
- 80%+ = excellent (viewers watch nearly all of it)
- 60-80% = good
- 40-60% = average for Shorts
- <40% = something is wrong (boring hook, bad pacing, wrong audience)

Why not use views? Views are driven by impressions and CTR (thumbnails/titles),
not content quality. Retention measures whether people actually liked it.

---

## Format Self-Learning (Example_Channel_4 Only)

This is the most critical learning loop for the Example_Channel_4 channel. The system runs 7 video formats
and must continuously learn which performs best, shift weight toward winners, and invent new formats.

### Step 5: Analyze Format Performance

After running `analyze_performance.py`, additionally run:

```
ACTIVE_CHANNEL=example_culture python execution/analyze_performance.py --group-by video_format
```

Look at average `avg_view_percentage` (retention) and `views` per `video_format`.

### Step 6: Update Format Weights

Edit `config/winning_strategy_example_culture.json`:

```json
{
  "video_format_weights": {
    "A_culture_observation": 1.0,
    "B_dating_guide": 2.5,
    "C_culture_shock_place": 2.0,
    "D_expat_perspective": 0.5,
    "E_meet_a_girl": 1.5,
    "F_survival_guide": 2.0,
    "G_will_shock_break": 1.8
  }
}
```

Weight rules:
- **>= 2.0**: Strong performer — will be picked ~50% more often
- **1.0**: Baseline — equal probability
- **<= 0.5**: Underperformer — still tested but rarely (~25% of baseline)
- **0**: Remove from pool (use with caution — needs 10+ samples)

### Step 7: Suggest New Formats (AI-Generated)

After ≥ 10 videos per format, ask Gemini to suggest a new format variation:

Prompt to use:
```
You are a YouTube Shorts strategist for a channel about women from different cultures.
These are the current video formats and their average retention:
[paste performance_findings_example_culture.json here]

Based on these results, suggest ONE new video format variation that:
1. Builds on what's working (high retention formats)
2. Is distinct enough to be a real experiment
3. Has a proven title formula from other successful channels
4. Targets the same audience: male viewers 18-34 curious about women from other cultures

Respond with JSON:
{"id": "H_new_format_name", "title_formula": "...", "example": "...", "rationale": "..."}
```

Add the result to `winning_strategy_example_culture.json` under `suggested_new_formats`:
```json
{
  "suggested_new_formats": [
    {"id": "H_comparison", "title_formula": "[Country A] Women VS [Country B] Women", "example": "Korean Woman VS Japanese Woman", "rationale": "Comparison videos drive debate comments from both sides"}
  ]
}
```

The ExperimentEngine will automatically inject these new formats into the pool on the next run.

### Format Learning Rules

- **Minimum samples**: 5 videos per format before changing weights
- **Maximum weight**: 3.0 (no format should dominate to prevent overfitting)
- **Keep all formats alive**: Never set weight to 0 until 20+ samples confirm it's dead
- **Log every change**: Append to `output/logs/format_learning_history.json`
- **New format testing**: Start new formats at weight 1.0, evaluate after 5 runs

## Learnings Log

Update this section when you discover patterns:

- **[Date]**: GuyNeural outperforms RogerNeural by +8% retention across 12 videos.
  auto_tune applied VOICE_NAME = "en-US-GuyNeural".
- **[Date]**: 50-55s videos retain 5% better than 55-60s. Duration reduced to 52s.

## Edge Cases

- **No data after fetch**: Video may be too new (<48h) or have no views yet.
  Both are expected. The script will skip and retry next time.
- **OAuth scope error**: The analytics scope was added after the uploader scope.
  Delete `config/youtube_token.pickle` and re-authorize — the new flow includes both.
- **Small channel caveat**: YouTube Analytics may not return `averageViewPercentage`
  for videos with very few views (<10). This is a YouTube API limitation.
  Those videos will show 0% retention — they won't be used in analysis.
