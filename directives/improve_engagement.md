# Directive: Optimize Video Engagement & Retention

## Goal
Diagnose audience drop-off points, elevate Average Percentage Viewed (APV), increase Click-Through Rate (CTR), and maximize viral distribution across YouTube Shorts.

---

## 🚀 Key Engagement Levers

### 1. Hook Impact (First 0–3 Seconds)
- **Rules**: Zero warmup, no pleasantries ("Hey guys", "Did you know"), start mid-sentence or with a sharp contradiction.
- **Tuning**: Edit `CHANNEL_TONE` in `channels/<channel>.json` or test hook styles via `execution/auto_tune.py`.
- **Target**: Hook retention > 70% at the 3-second mark.

### 2. Animated Captions (`core/ass_generator.py`)
- **Visual Rhythm**: 2–3 words per highlighted phrase chunk.
- **Styling**: Configure `"CAPTION_STYLE"` in channel JSON (`"yellow_glow"`, `"classic_bold"`, `"neon_cyan"`).
- **Positioning**: Default is lower-center safe zone (`SUBTITLE_Y = 1100`).

### 3. Visual Pacing & B-Roll Precision (`core/video_clipper.py`)
- **Duration**: Clips trimmed to 2.5s–4.0s minimums to prevent strobe-like editing.
- **Provider**: Set `"STOCK_PROVIDER": "pexels"` or `"pixabay"` in channel JSON.
- **Validation**: Enable `SKIP_CLIP_VALIDATION=False` in `.env` to enforce semantic Gemini Vision clip relevance checks.

### 4. Audio Mastering & Background Music (`core/agents/music_director.py`)
- **Voice Normalization**: Speech is auto-normalized to `-14 LUFS`.
- **Dynamic Ducking**: BGM volume ducks during speech and swells during pauses.
- **Mood Matching**: Set `"DEFAULT_BGM_MOOD"` (`"sci-fi"`, `"cinematic"`, `"dark"`, `"ambient"`, `"trap"`) in channel JSON.

---

## Closed-Loop Optimization Workflow

1. Ingest real YouTube Analytics data:
   ```bash
   ACTIVE_CHANNEL=demo_channel python execution/fetch_analytics.py
   ```
2. Analyze high-performing parameters:
   ```bash
   ACTIVE_CHANNEL=demo_channel python execution/analyze_performance.py
   ```
3. Run algorithmic parameter auto-tuning:
   ```bash
   ACTIVE_CHANNEL=demo_channel python execution/auto_tune.py
   ```

