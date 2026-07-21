# Directive: Improve Video Engagement

## Goal
Diagnose why a video isn't performing well and apply targeted fixes to boost retention, click-through rate, and watch time.

## Engagement Levers (in order of ROI)

### 1. Captions (Highest Impact)
**File**: `core/ass_generator.py`
- Words per group: `CHUNK_SIZE = 3` (2-3 words at a time is optimal)
- Font size: 105px Arial Black (increase to 115 for more punch)
- Animation: bounce from 80% → 110% → 100% over 140ms
- First word color: yellow (`\c&H00FFFF&`), rest white (`\c&HFFFFFF&`)
- Position: `Alignment=2` = bottom-center

**What bad captions look like**: Single word at a time (too fast, eye fatigue), tiny font, no color contrast, no animation.

### 2. Hook (Second Highest Impact)
**File**: `core/script_generator.py` — update the prompt
- Hook must be ≤10 words
- Must create SHOCK or FOMO in the first 1.5 seconds
- No "Hey guys", "Did you know", or soft openers
- Test: read the hook aloud — does it make you want to hear the next sentence?

### 3. Background Gameplay
**File**: `core/video_clipper.py` — `HIGH_ENERGY_BACKGROUNDS` list
- Add more queries to the pool for variety
- Prefer: parkour fails, car stunts, satisfying loops, speed-run highlights
- Avoid: slow-paced open world, tutorial content
- Speed: `setpts=0.909*PTS` = 1.1x speed (increase to `0.833*PTS` for 1.2x)

### 4. PIP Overlays
**File**: `core/video_assembler.py` — `merge_audio_video()`
- Overlay width: `OVL_W = 860` (decrease to 760 to show more gameplay)
- Slide-in duration: `SLIDE_DUR = 0.3` (decrease to 0.2 for snappier feel)
- Border thickness: `BORDER = 7` (increase to 10 for more "frame" look)

### 5. Voiceover Energy
**File**: `core/voiceover.py`
- Rate: `+20%` (increase to `+30%` for faster delivery)
- Voice options: `GuyNeural` (passion), `RogerNeural` (lively), `EricNeural` (rational)

### 6. BGM Energy
**File**: `core/video_assembler.py` — `get_bgm()`
- Current BGM volume: `0.18` — increase to `0.25` for more energy
- To re-download fresh BGM: delete `output/videos/bgm.mp3`
- Search query: currently `"phonk drift music no copyright gaming background"`

## Diagnosis Steps
1. Watch the latest output video in `output/videos/`
2. Note the timestamp where you feel like clicking away — that's the problem area
3. Check: is it captions (can't read fast enough)? hook (didn't grab)? visuals (boring overlap)?
4. Make ONE change at a time and re-run to A/B compare

## Known Issues
- **PIP overlays look "unnatural"**: The slide-in animation should fix this. If it still looks flat, reduce overlay size (`OVL_W`) so more gameplay is visible around it.
- **Captions overlapping**: Caused by CHUNK_SIZE being too large relative to speech pace. Reduce to `CHUNK_SIZE = 2` if words run together.
- **Captions not visible**: Check subtitle path escaping in `merge_audio_video()`. The `ffmpeg_sub` path must have backslashes escaped as `\\:`.
