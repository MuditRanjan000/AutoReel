# Directive: Run Full AutoReel Pipeline

## Goal
Produce one complete viral YouTube Short end-to-end: fetch a trending story, write a script, generate voiceover + subtitles, download footage, assemble the final video.

## Inputs
- None required. The pipeline is self-contained and auto-selects the best trending story.
- Optional: `STORY_OVERRIDE` env var — set to a story title to skip trend fetching.

## Tools / Scripts to Use
Run in order:
1. `execution/run_pipeline.py` — orchestrates all steps

Or run individual steps manually:
1. `execution/fetch_story.py` — Step 1: Fetch + rank trending stories
2. `execution/generate_script.py <story_json_path>` — Step 2: Write script via Gemini
3. `execution/generate_voiceover.py <script_json_path>` — Step 3: TTS + Whisper subtitles
4. `execution/download_clips.py <script_json_path> <run_id>` — Step 4: Download footage
5. `execution/assemble_video.py <run_id>` — Step 5: Merge everything into final MP4

## Outputs
- `output/videos/<run_id>_final.mp4` — final video ready for upload
- `output/thumbnails/<run_id>_thumb.jpg` — thumbnail
- `output/logs/<run_id>_summary.json` — run metadata (always kept)

## Post-Upload Cleanup
After a **successful YouTube upload**, the pipeline automatically deletes all
large generated files for that run to save disk space. Only the tiny
`_summary.json` log is kept as a record.

**Files deleted after upload:**
- `output/videos/<run_id>*.mp4` — raw downloads, merged, final video
- `output/videos/<run_id>*.mp3` — voiceover audio
- `output/videos/<run_id>*.ass` — subtitle file
- `output/videos/clips/<run_id>*` — all processed clip files
- `output/thumbnails/<run_id>*.jpg` — thumbnail

**Cleanup is skipped if:** upload is disabled or the upload fails (so you
can fix and retry without losing the assembled video).

**Override flags:**
```
python execution/run_pipeline.py --no-cleanup         # keep files even after upload
python execution/run_pipeline.py --cleanup-only <id>  # manually clean a past run
```

**Note:** `output/videos/bgm.mp3` is intentionally kept — it's a shared
background music file reused across all runs to avoid re-downloading.

## Edge Cases & Learnings
- **Gemini quota (20 req/day free tier)**: The trend fetcher auto-falls back to a local keyword scorer. The script generator will raise a clear error if quota is hit — wait until midnight PT or add a second API key to `.env`.
- **yt-dlp timeouts**: Individual clip downloads have a 120s timeout. If a query returns nothing, the pipeline skips that overlay and continues with fewer PIP clips.
- **Whisper FP16 warning**: On CPU-only machines, whisper runs in FP32 mode. This is expected and harmless — just slower (~60s for a 55s clip).
- **FFmpeg filter_complex errors**: If the video merge fails, check `output/logs/pipeline.log` for the full stderr. The most common cause is a malformed subtitle path (backslashes on Windows — the assembler escapes them).
- **BGM download**: The BGM (`output/videos/bgm.mp3`) is downloaded once and cached. Delete it to force a fresh download.
- **edge-tts `NoAudioReceived`**: Happens if the selected voice name is invalid. Valid voices: `en-US-GuyNeural` (passion), `en-US-RogerNeural` (lively), `en-US-ChristopherNeural` (authority). See `directives/change_voice.md`.

## Configuration
All tunable settings are in `config/settings.py`:
- `GEMINI_MODEL` — current: `gemini-flash-latest`
- `VIDEO_DURATION_SECONDS` — current: 55
- `VOICE` / `RATE` in `core/voiceover.py`
- `HIGH_ENERGY_BACKGROUNDS` list in `core/video_clipper.py`
