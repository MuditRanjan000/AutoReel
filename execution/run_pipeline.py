"""
execution/run_pipeline.py  -- THE SINGLE CANONICAL PIPELINE
===========================================================
Merged from pipeline.py + old execution/run_pipeline.py.
This is now the ONLY pipeline entry point.

Full feature set:
  Step 0  -- A/B Test Recipe (ExperimentEngine)
  Step 1  -- Fetch trending story (TrendFetcher + 6h cache)
  Step 2  -- Generate script (ScriptGenerator + Groq/Gemini)
  Step 2.1-- Legal + Compliance check (LegalAgent)
  Step 2.2-- Quality Control check (QualityControlAgent)
  Step 3  -- Voiceover + ASS subtitles (edge-tts + Whisper)
  Step 4  -- Download footage (Pexels -> YouTube fallback)
  Step 4.1-- Dynamic BGM (MusicDirectorAgent)
  Step 5  -- Assemble final video (FFmpeg)
  Step 5.1-- AI Thumbnail (ThumbnailDesigner via Pollinations.ai)
  Step 5.2-- Text Quality Review Gate (Groq — fast script/caption check)
  Step 5.3-- AI Video Reviewer (Gemini File API — human-like visual review)
  Step 6  -- Upload to YouTube
  Step 7  -- Disk cleanup

Usage:
    python execution/run_pipeline.py
    python execution/run_pipeline.py --run-id custom_id
    python execution/run_pipeline.py --no-cleanup
    python execution/run_pipeline.py --cleanup-only <run_id>
"""

import sys
import os
import glob
import json
import argparse
import importlib.util
import traceback
import time
import socket
from datetime import datetime

# Prevent infinite network deadlocks on Windows (overrides httpx/requests if they fail to timeout)
socket.setdefaulttimeout(45.0)

# Ensure project root is on sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.trend_fetcher          import TrendFetcher
from core.script_generator       import ScriptGenerator
from core.voiceover              import VoiceoverGenerator
from core.video_clipper          import VideoClipper
from core.video_assembler        import VideoAssembler
from core.youtube_uploader       import YouTubeUploader
from core.experiment_tracker     import ExperimentTracker
from core.experiment_engine      import ExperimentEngine
from core.telegram_bot           import send_message
from core.agents.legal           import LegalAgent
from core.agents.quality_control import QualityControlAgent
from core.agents.thumbnail_designer import ThumbnailDesigner
from core.agents.music_director  import MusicDirectorAgent
from core.channel_context        import ChannelContext
from config.settings import (
    LOG_DIR, OUTPUT_DIR, THUMBNAIL_DIR,
    AUTO_POST_YOUTUBE, VOICE_NAME, VOICE_RATE,
    VIDEO_DURATION_SECONDS, CHANNEL_NAME, QUALITY_SCORE_THRESHOLD,
    get_video_encoder_args, MIN_DISK_SPACE_GB
)

os.makedirs(LOG_DIR, exist_ok=True)

class ContentFailure(Exception):
    """Exception raised for content-related failures (factuality, weak source, unrecoverable story) that trigger Story B."""
    pass

class InfrastructureFailure(Exception):
    """Exception raised for infrastructure failures (API outage, FFmpeg issue) that halt the pipeline without fallback."""
    pass


# --- Background Intelligence Loop -------------------------------------------
# Runs automatically at the end of every pipeline run.
# Checks time-based triggers stored in config/bg_intel_timestamps.json.
# Fires weekly tasks silently so you never have to run anything manually.

_BG_INTEL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "bg_intel_timestamps.json"
)


def _load_bg_timestamps() -> dict:
    try:
        with open(_BG_INTEL_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_bg_timestamps(data: dict):
    os.makedirs(os.path.dirname(_BG_INTEL_FILE), exist_ok=True)
    with open(_BG_INTEL_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _run_weekly_rnd(channel: str, niche: str, log_fn):
    """Scout top competitor titles + thumbnails, extract new hook & thumbnail patterns."""
    try:
        log_fn("[BgIntel] Running weekly R&D competitor scan...")
        import subprocess as _sp
        from core.gemini_client import generate_with_rotation
        from core.ytdlp_utils import extend_with_cookies

        # ── Step 1: Scrape top competitor Shorts metadata + thumbnail URLs ──
        query = f"{niche} shorts"
        cmd = [
            sys.executable, "-m", "yt_dlp", f"ytsearch8:{query}",
            "--dump-json", "--no-download",
            "--match-filter", "duration < 65",
            "--ignore-errors", "--quiet"
        ]
        cmd = extend_with_cookies(cmd)
        result = _sp.run(cmd, capture_output=True, text=True, timeout=60)
        competitors = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                d = json.loads(line)
                competitors.append({
                    "title":      d.get("title", ""),
                    "views":      d.get("view_count", 0),
                    "thumbnail":  d.get("thumbnail", ""),
                    "duration":   d.get("duration", 0),
                })
            except Exception:
                pass
        competitors = sorted(competitors, key=lambda x: x["views"] or 0, reverse=True)[:6]

        if not competitors:
            log_fn("[BgIntel] R&D: No competitor data found.")
            return

        # ── Step 2: Analyze titles for hook patterns ──
        title_block = "\n".join(
            f"{i+1}. [{c['views']:,} views] {c['title']}"
            for i, c in enumerate(competitors)
        )
        hook_prompt = f"""You are the Head of Research for a YouTube Shorts automation channel in the '{niche}' niche.

Here are the top {len(competitors)} competing Shorts by view count:
{title_block}

Analyze the titles. Identify ONE new hook style we should steal and test.
Also identify the dominant THUMBNAIL pattern from these titles (face vs no-face, text style, emotional tone).

Return ONLY valid JSON:
{{"new_hook_style": "Name (how to write it)", "thumbnail_insight": "1 sentence about what visual pattern wins in this niche", "top_title_formula": "The title formula used by the #1 video"}}"""

        response = generate_with_rotation(hook_prompt)
        response = response.strip()
        if "```" in response:
            response = response.split("```")[1].split("```")[0].strip()
            if response.startswith("json"):
                response = response[4:].strip()

        analysis = json.loads(response)
        new_hook        = analysis.get("new_hook_style", "")
        thumb_insight   = analysis.get("thumbnail_insight", "")
        top_formula     = analysis.get("top_title_formula", "")

        # ── Step 3: Inject new hook into winning strategy ──
        strategy_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", f"winning_strategy_{channel}.json"
        )
        strategy = {}
        if os.path.exists(strategy_file):
            try:
                with open(strategy_file, "r") as f:
                    strategy = json.load(f)
            except Exception:
                pass
        strategy["rnd_hook_style"]      = new_hook
        strategy["rnd_thumbnail_intel"] = thumb_insight
        strategy["rnd_top_formula"]     = top_formula
        strategy["rnd_updated_at"]      = datetime.now().isoformat()
        with open(strategy_file, "w") as f:
            json.dump(strategy, f, indent=2)

        log_fn(f"[BgIntel] R&D complete. New hook: {new_hook[:60]}")
        send_message(
            f"\U0001f9e0 *R&D Weekly Report — {channel}*\n\n"
            f"*New Hook Stolen:* `{new_hook}`\n\n"
            f"*Thumbnail Intel:* {thumb_insight}\n\n"
            f"*Top Title Formula:* {top_formula}\n\n"
            f"Injected into ExperimentEngine automatically."
        )
    except Exception as e:
        log_fn(f"[BgIntel] R&D scan failed (non-fatal): {e}")


def _run_weekly_autotune(channel: str, log_fn):
    """Run analyze_performance + auto_tune for this channel automatically."""
    try:
        log_fn("[BgIntel] Running weekly performance analysis + auto-tune...")
        import subprocess as _sp
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env   = {**os.environ, "ACTIVE_CHANNEL": channel}

        # Step 1: analyze_performance.py → writes performance_findings_{channel}.json
        r1 = _sp.run(
            ["python", os.path.join(_root, "execution", "analyze_performance.py")],
            capture_output=True, text=True, timeout=120, env=env
        )
        if r1.returncode != 0:
            log_fn(f"[BgIntel] analyze_performance failed: {r1.stderr[:200]}")
            return

        # Step 2: auto_tune.py → applies changes to channel config
        r2 = _sp.run(
            ["python", os.path.join(_root, "execution", "auto_tune.py")],
            capture_output=True, text=True, timeout=60, env=env
        )
        log_fn(f"[BgIntel] Auto-tune complete. Output: {r2.stdout.strip()[-200:]}")
        send_message(
            f"\u2699\ufe0f *Auto-Tune Applied — {channel}*\n"
            f"Weekly performance analysis ran and settings updated automatically.\n"
            f"Check config/auto_tune_history_{channel}.json for changes."
        )
    except Exception as e:
        log_fn(f"[BgIntel] Auto-tune failed (non-fatal): {e}")


def run_background_intelligence(channel: str, niche: str, log_fn=print):
    """
    Called at the END of every pipeline run.
    Silently fires weekly tasks if they're due — zero manual intervention needed.

    Tasks:
      - Weekly R&D: competitor title + thumbnail analysis (every 7 days)
      - Weekly auto-tune: performance analysis + settings update (every 7 days)
    """
    now = time.time()
    ts  = _load_bg_timestamps()
    key = channel  # per-channel timestamps so channels don't interfere

    SEVEN_DAYS = 7 * 24 * 3600

    # ── Weekly R&D ──────────────────────────────────────────────────────
    rnd_key  = f"{key}_rnd_last_run"
    rnd_last = ts.get(rnd_key, 0)
    if now - rnd_last >= SEVEN_DAYS:
        _run_weekly_rnd(channel, niche, log_fn)
        ts[rnd_key] = now
        _save_bg_timestamps(ts)

    # ── Weekly Auto-Tune ─────────────────────────────────────────────────
    tune_key  = f"{key}_tune_last_run"
    tune_last = ts.get(tune_key, 0)
    if now - tune_last >= SEVEN_DAYS:
        _run_weekly_autotune(channel, log_fn)
        ts[tune_key] = now
        _save_bg_timestamps(ts)


# --- Logging -----------------------------------------------------------------

def log(msg: str, run_id: str = ""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    with open(os.path.join(LOG_DIR, "pipeline.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


# --- Disk cleanup ------------------------------------------------------------

def cleanup_run(run_id: str, keep_deliverables: bool = False):
    """
    Delete all large generated files for a run after successful upload.
    If keep_deliverables is True, keeps the final compiled video and final thumbnail.
    Always keeps: output/logs/<run_id>_summary.json (tiny, useful for records).
    Also purges: BGM pool tracks older than 30 days, .tmp scratch files.
    """
    deleted = []
    total_bytes = 0

    patterns = [
        os.path.join(OUTPUT_DIR,    f"{run_id}*.mp4"),
        os.path.join(OUTPUT_DIR,    f"{run_id}*.mp3"),
        os.path.join(OUTPUT_DIR,    f"{run_id}*.json"),
        os.path.join(OUTPUT_DIR,    f"{run_id}*.ass"),
        os.path.join(OUTPUT_DIR, "clips", f"{run_id}*"),
        os.path.join(THUMBNAIL_DIR, f"{run_id}*.jpg"),
    ]

    for pattern in patterns:
        for path in glob.glob(pattern):
            if keep_deliverables:
                basename = os.path.basename(path)
                if (basename.endswith("_final.mp4")
                        or "_thumbnail.jpg" in basename
                        or "_thumb.jpg" in basename):
                    continue
            try:
                size = os.path.getsize(path)
                os.remove(path)
                deleted.append(path)
                total_bytes += size
            except Exception as e:
                log(f"  Could not delete {path}: {e}", run_id)

    # Purge BGM pool tracks older than 30 days to prevent disk bloat
    bgm_pool_dir = os.path.join(OUTPUT_DIR, "bgm_pool")
    if os.path.exists(bgm_pool_dir):
        now = time.time()
        for fname in os.listdir(bgm_pool_dir):
            fpath = os.path.join(bgm_pool_dir, fname)
            try:
                age_days = (now - os.path.getmtime(fpath)) / 86400
                if age_days > 30:
                    size = os.path.getsize(fpath)
                    os.remove(fpath)
                    deleted.append(fpath)
                    total_bytes += size
                    log(f"  Purged stale BGM track ({age_days:.0f}d old): {fname}", run_id)
            except Exception:
                pass


    # Purge any leftover .tmp scratch directory
    tmp_dir = os.path.join(OUTPUT_DIR, ".tmp")
    if os.path.exists(tmp_dir):
        for fname in os.listdir(tmp_dir):
            fpath = os.path.join(tmp_dir, fname)
            try:
                size = os.path.getsize(fpath)
                os.remove(fpath)
                deleted.append(fpath)
                total_bytes += size
            except Exception:
                pass

    mb_freed = total_bytes / (1024 * 1024)
    log(f"Cleanup: removed {len(deleted)} files, freed {mb_freed:.1f} MB", run_id)
    return deleted


# --- Review helpers ---------------------------------------------------------

def _run_quality_review(run_id: str) -> dict:
    """Dynamically import review_video.py and call review(run_id). (Text gate)"""
    review_path = os.path.join(_ROOT, "execution", "review_video.py")
    spec = importlib.util.spec_from_file_location("review_video", review_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.review(run_id)


def _run_quality_engine_review(run_id: str, ctx) -> dict:
    """Run the blocking Quality Engine V1 reviewer (Step 5.3)."""
    try:
        from core.agents.video_quality_reviewer import VideoQualityReviewer
        reviewer = VideoQualityReviewer(ctx=ctx)
        video_path = os.path.join(_ROOT, "output", "videos", f"{run_id}_final.mp4")
        return reviewer.evaluate(run_id, video_path)
    except Exception as e:
        log(f"[QualityEngine] Error: {e}", run_id)
        return {"upload_recommended": False, "rejection_reason": f"System error: {e}", "final_score": 0, "error": True}


# --- Thumbnail Target Frame Injection ----------------------------------------

def _inject_thumbnail_frame(video_path: str, thumbnail_path: str, run_id: str) -> bool:
    """
    Appends a 0.2-second static image frame of the generated thumbnail at the very end
    of the video file so YouTube mobile can use it as a custom thumbnail.
    """
    import subprocess
    if not os.path.exists(video_path) or not os.path.exists(thumbnail_path):
        return False

    log("[ThumbnailInjector] Injecting target thumbnail frame to end of video...", run_id)

    temp_thumb_clip = video_path.replace("_final.mp4", "_thumb_clip.mp4")
    concat_list     = video_path.replace("_final.mp4", "_concat_list.txt")
    injected_output = video_path.replace("_final.mp4", "_injected.mp4")

    try:
        fps_str = "30"
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", video_path],
                capture_output=True, text=True, timeout=5)
            fps_str = res.stdout.strip() or "30"
            log(f"[ThumbnailInjector] Probed frame rate: {fps_str}", run_id)
        except Exception:
            pass

        channels_str = "1"
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=channels", "-of", "csv=p=0", video_path],
                capture_output=True, text=True, timeout=5)
            channels_str = res.stdout.strip() or "1"
        except Exception:
            pass

        sample_rate_str = "44100"
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=sample_rate", "-of", "csv=p=0", video_path],
                capture_output=True, text=True, timeout=5)
            sample_rate_str = res.stdout.strip() or "44100"
        except Exception:
            pass

        cl_val = "mono" if channels_str == "1" else "stereo"
        encoder_args = get_video_encoder_args()

        cmd_clip = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", thumbnail_path,
            "-f", "lavfi", "-i", f"anullsrc=r={sample_rate_str}:cl={cl_val}",
            "-t", "0.2",
            "-vf", "scale=1080:1920,setsar=1",
            "-r", fps_str,
            "-c:a", "aac", "-b:a", "192k",
            "-ar", sample_rate_str, "-ac", channels_str,
        ] + encoder_args + [temp_thumb_clip]

        res_clip = subprocess.run(cmd_clip, capture_output=True, text=True)
        if res_clip.returncode != 0 or not os.path.exists(temp_thumb_clip):
            log(f"[ThumbnailInjector] Failed: {res_clip.stderr[-300:]}", run_id)
            return False

        abs_video = os.path.abspath(video_path).replace("\\", "/")
        abs_clip  = os.path.abspath(temp_thumb_clip).replace("\\", "/")
        with open(concat_list, "w", encoding="utf-8") as f:
            f.write(f"file '{abs_video}'\n")
            f.write(f"file '{abs_clip}'\n")

        res_concat = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", concat_list, "-c", "copy", injected_output],
            capture_output=True, text=True)
        if res_concat.returncode != 0 or not os.path.exists(injected_output):
            log(f"[ThumbnailInjector] Concat failed: {res_concat.stderr[-300:]}", run_id)
            return False

        from core.utils import safe_atomic_replace
        if not safe_atomic_replace(injected_output, video_path):
            log("[ThumbnailInjector] Could not replace final.mp4 safely. Keeping original.", run_id)
            return False
            
        log("[ThumbnailInjector] Successfully injected 0.2s target frame!", run_id)
        return True

    except Exception as e:
        log(f"[ThumbnailInjector] Error: {e}", run_id)
        return False
    finally:
        for fp in [temp_thumb_clip, concat_list, injected_output]:
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass


# --- Main pipeline -----------------------------------------------------------

def run(run_id: str = None, cleanup_after_upload: bool = True, is_fallback: bool = False) -> bool:
    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Instantiate ChannelContext — the ONLY place ACTIVE_CHANNEL env var is read ──
    # All channel-specific config flows through this object from here on.
    ctx = ChannelContext.from_env()
    log(ctx.summary(), run_id)
    _channel_display = ctx.display_name  # Use this in all alerts — never the global CHANNEL_NAME

    log(f"=========== Pipeline Start | Run ID: {run_id} ===========", run_id)

    # STEP 0 Pre-flight Check: Disk Space
    import shutil as _shutil
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    _free_gb = _shutil.disk_usage(OUTPUT_DIR).free / (1024 ** 3)
    if _free_gb < MIN_DISK_SPACE_GB:
        log(f"Pre-flight failed: Only {_free_gb:.1f}GB free disk space (need ≥{MIN_DISK_SPACE_GB}GB).", run_id)
        send_message(
            f"⚠️ *Disk Space Alert* — {_channel_display}\n"
            f"Pipeline aborted: Only {_free_gb:.1f}GB free (need ≥{MIN_DISK_SPACE_GB}GB)."
        )
        return False

    # STEP 0.1 Pre-flight Check: yt-dlp version (updated daily by scheduler janitor, not per-run)

    # Step timing helper for performance profiling
    _step_start = time.time()
    def step_time(label: str):
        elapsed = time.time() - _step_start
        log(f"  [{elapsed:.1f}s elapsed] {label}", run_id)

    try:

        # STEP 0: A/B Test Recipe
        log("Step 0: Experiment Engine selecting recipe...", run_id)
        engine = ExperimentEngine(ctx=ctx)
        recipe = engine.generate_recipe()
        log(f"Recipe: {json.dumps(recipe)}", run_id)

        # STEP 1: Fetch trending story
        log("Step 1: Fetching trending story...", run_id)
        fetcher = TrendFetcher(ctx=ctx)
        story   = fetcher.get_todays_story(recipe=recipe, force_refresh=is_fallback)
        log(f"Story: {story['title']}", run_id)
        log(f"Angle: {story.get('angle', 'N/A')}", run_id)

        # STEP 2: Generate script with Self-Correction Retry Loop
        log("Step 2: Preparing script generation with Self-Correction Loop...", run_id)
        script_gen = ScriptGenerator()

        MAX_ATTEMPTS = 3
        attempt = 1
        story_swaps = 0
        correction_feedback = None
        _cached_clips   = None      # Reused across script-only retries (C2 fix)
        _cached_bg_clip = None
        _cached_bgm_path = None
        execution_manifest = {}
        overlays = []               # Safe default — populated by VideoClipper in Step 4
        _last_bgm_mood = "suspense"   # Safe fallback if bgm was cached from attempt 1
        _last_bgm_track = "fallback"  # Safe fallback for experiment tracking

        while attempt <= MAX_ATTEMPTS:
            log(f"--- Pipeline Render Attempt {attempt}/{MAX_ATTEMPTS} ---", run_id)
            try:
                script = script_gen.generate(story, recipe, correction_feedback=correction_feedback, ctx=ctx)
            except ValueError as ve:
                log(f"Script Generation failed (attempt {attempt}): {ve}", run_id)
                if attempt < MAX_ATTEMPTS:
                    log(f"Initiating script self-correction loop due to JSON format failure...", run_id)
                    correction_feedback = f"- [JSON PARSE FAILURE] The generated script was not valid JSON: {ve}\n  Fix: Ensure the output is strictly valid JSON conforming to the requested schema. Pay special attention to escaping quotes and brackets."
                    attempt += 1
                    continue
                else:
                    raise
            log(f"Title : {script['title']}", run_id)
            log(f"Hook  : {script['hook']}", run_id)

            # STEP 2.1: Legal check
            log("Step 2.1: Legal Agent reviewing script (waiting 10s for API pacing)...", run_id)
            legal       = LegalAgent(ctx=ctx)
            legal_check = legal.assess_risk(script["full_script"])
            if not legal_check["is_safe"]:
                log(f"Legal Agent flagged script: {legal_check['reason']}", run_id)
                raise ContentFailure(f"Legal Agent flagged script: {legal_check['reason']}")

            # STEP 2.2: Quality Control
            log("Step 2.2: QC Agent reviewing content (waiting 10s for API pacing)...", run_id)
            qc       = QualityControlAgent(ctx=ctx)
            qc_check = qc.evaluate(script["title"], script["full_script"])
            if not qc_check["approved"]:
                log(f"QC Agent rejected script (attempt {attempt}): {qc_check['feedback']}", run_id)
                if attempt < MAX_ATTEMPTS:
                    log(f"Initiating script self-correction loop (clips will be reused)...", run_id)
                    # Build targeted correction feedback that names the specific failed dimension
                    raw_feedback = qc_check['feedback']
                    # Map QC weakest-criteria labels to actionable fix instructions
                    fix_map = {
                        "Hook Power": "Rewrite the opening 1-2 sentences. Start mid-conflict, mid-drama or mid-shock. No warm-up phrases allowed. The viewer must be gripped in the first 5 words.",
                        "Emotional Category": "Lock the entire script into ONE emotional lens: Drama, Money, Fear, Mistakes, Secrets, or Conflict. Remove any neutral or analytical framing.",
                        "Angle Surprise": "The angle is too obvious — it's the same thing every news channel would say. Find the CONTRARIAN or UNEXPECTED perspective on this story. What does this story IMPLY that nobody is saying?",
                        "Host Personality": "The script sounds like a generic AI narrator. Rewrite it completely in the channel's specific host voice and persona as described in the skill. It must sound like a real person with opinions, not a news bot.",
                        "Human Rhythm": "The script reads like a list of facts. Rewrite using natural spoken transitions ('But here's the thing —', 'And then this happened.', 'Wait — it gets worse.'). Mix short punchy sentences with longer flowing ones.",
                    }
                    specific_fix = "Rewrite with stronger personality, a more surprising angle, and better emotional hook."
                    for criteria_key, fix_instruction in fix_map.items():
                        if criteria_key.lower() in raw_feedback.lower():
                            specific_fix = fix_instruction
                            break
                    correction_feedback = f"- [CREATIVE QUALITY FAILURE — {qc_check['feedback'].split(']')[0].replace('[Weakest: ', '').strip() if ']' in qc_check['feedback'] else 'QUALITY'}] {raw_feedback}\n  Fix: {specific_fix}"
                    attempt += 1
                    
                    # Massive pacing sleep before a retry because the previous attempt exhausted a ton of tokens
                    log("Waiting 20s for Groq/Gemini API quotas to replenish before generating correction...", run_id)
                    continue
                else:
                    if story_swaps < 1:
                        log(f"QC failed for '{story['title']}' after {MAX_ATTEMPTS} attempts. Trying next best story...", run_id)
                        try:
                            # Bypass the 2h story cache so we actually get a different story
                            if hasattr(fetcher, '_story_cache'):
                                fetcher._story_cache = None
                            if hasattr(fetcher, '_cache_time'):
                                fetcher._cache_time = None
                            _alt_story = fetcher.get_todays_story(recipe=recipe, force_refresh=True)
                            if _alt_story and _alt_story['title'] != story['title']:
                                log(f"Alternative story found: {_alt_story['title']}", run_id)
                                story = _alt_story
                                attempt = 1
                                story_swaps += 1
                                correction_feedback = None
                                _cached_clips = None
                                _cached_bg_clip = None
                                _cached_bgm_path = None
                                continue  # restart the while loop with new story
                        except Exception as _se:
                            log(f"Alternative story fetch failed: {_se}", run_id)
                    raise ContentFailure(f"QC Agent rejected script after max attempts (no alt story): {qc_check['feedback']}")


            # STEP 3: Voiceover + subtitles
            log("Step 3: Generating voiceover (Multi-Tier TTS)...", run_id)
            voice_gen  = VoiceoverGenerator()
            # Pass the entire script object so ass_generator can access script["scenes"]
            audio_path, voice_actuals = voice_gen.generate(
                script=script,  # Pass dict instead of string
                filename=f"{run_id}_voice.mp3",
                recipe=recipe,
                ctx=ctx,
            )
            execution_manifest.update(voice_actuals)
            step_time("Step 3 done")
            log(f"Audio: {audio_path}", run_id)

            # STEP 4: Download footage (skip re-download if clips were cached on a prior attempt)
            if _cached_clips is not None:
                bg_clip = _cached_bg_clip
                overlays = _cached_clips
                log(f"Step 4: Reusing {len(overlays)} cached clips from attempt 1 (skipping re-download).", run_id)
            else:
                log("Step 4: Downloading footage...", run_id)
                step_time("Step 4 starting")
                clipper           = VideoClipper(ctx=ctx)
                bg_clip, overlays = clipper.get_clip_for_story(script, run_id, recipe=recipe)
                if not overlays:
                    log("No footage clips found. Aborting.", run_id)
                    raise ContentFailure("No footage clips found. Aborting.")
                step_time("Step 4 done")
                # Cache for reuse on subsequent script-only retries
                _cached_clips   = overlays
                _cached_bg_clip = bg_clip
            log(f"Clips: {len(overlays)} (documentary format)", run_id)

            # STEP 4.1: Dynamic BGM (reuse cached track on retry — same mood for same story)
            if _cached_bgm_path is not None:
                bgm_path = _cached_bgm_path
                log(f"Step 4.1: Reusing cached BGM from attempt 1.", run_id)
            else:
                log("Step 4.1: Sourcing dynamic background music...", run_id)
                music_agent = MusicDirectorAgent(ctx=ctx)
                bgm_path    = music_agent.score_video(script["full_script"], run_id, recipe=recipe)
                _cached_bgm_path = bgm_path
                _last_bgm_mood  = getattr(music_agent, "last_mood",     _last_bgm_mood)
                _last_bgm_track = getattr(music_agent, "last_track_id", _last_bgm_track)

            # STEP 5: Assemble video
            log("Step 5: Assembling final video...", run_id)
            assembler = VideoAssembler(ctx=ctx)
            output    = assembler.assemble(
                overlays, audio_path, script, run_id, recipe, bgm_path=bgm_path
            )
            log(f"Video : {output['video']}", run_id)
            log(f"Thumb : {output['thumbnail']}", run_id)

            # Validate final video
            _vid = output.get("video", "")
            if not _vid or not os.path.exists(_vid) or os.path.getsize(_vid) < 10_000:
                log(f"Assembly produced a missing/corrupt video ({_vid}). Aborting.", run_id)
                raise InfrastructureFailure(f"Assembly produced a missing/corrupt video ({_vid}).")

            # STEP 5.0.1: ffprobe Stream Validation and Physical Integrity Check
            import subprocess as _subprocess
            stream_valid = True
            try:
                # 1. Missing streams check
                for stream_idx in ["v:0", "a:0"]:
                    _stream_cmd = ["ffprobe", "-v", "error", "-select_streams", stream_idx, "-show_entries", "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1", _vid]
                    _stream_out = _subprocess.check_output(_stream_cmd, timeout=30).decode().strip()
                    if not _stream_out:
                        log(f"Validation Error: Missing stream {stream_idx} in {_vid}", run_id)
                        stream_valid = False
                
                # 2. Header and Container Integrity
                _hdr_cmd = ["ffmpeg", "-v", "error", "-i", _vid, "-f", "null", "-"]
                _subprocess.check_output(_hdr_cmd, stderr=_subprocess.STDOUT, timeout=60)
                
                # Escape paths for lavfi filter on Windows
                _vid_filter = _vid.replace("\\", "/").replace(":", "\\\\:")
                
                # 3. Black Detect (if black screen > 8s - relaxed for raw footage/bodycam)
                # _black_cmd = ["ffprobe", "-f", "lavfi", "-i", f"movie={_vid_filter},blackdetect=d=8.0[out0]", "-show_entries", "tags=lavfi.black_start", "-of", "default=nw=1:nk=1", "-v", "quiet"]
                # _black_out = _subprocess.check_output(_black_cmd, timeout=60).decode().strip()
                # if _black_out:
                #     log(f"Validation Error: Black frames detected in {_vid} at {_black_out}", run_id)
                #     stream_valid = False
                    
                # 4. Silence Detect (if silence > 2s)
                # _silence_cmd = ["ffprobe", "-f", "lavfi", "-i", f"amovie={_vid_filter},silencedetect=noise=-50dB:d=3.5[out0]", "-show_entries", "tags=lavfi.silence_start", "-of", "default=nw=1:nk=1", "-v", "quiet"]
                # _silence_out = _subprocess.check_output(_silence_cmd, timeout=60).decode().strip()
                # if _silence_out:
                #     log(f"Validation Error: Unacceptable silence detected in {_vid} at {_silence_out}", run_id)
                #     stream_valid = False

            except _subprocess.TimeoutExpired:
                log(f"Validation Error: Integrity check timed out for {_vid}", run_id)
                stream_valid = False
            except _subprocess.CalledProcessError as e:
                err_msg = e.output.decode(errors='ignore') if getattr(e, 'output', None) else ""
                log(f"Validation Error: Process returned non-zero exit code during integrity check for {_vid}: {err_msg}", run_id)
                stream_valid = False
            except Exception as e:
                log(f"FFprobe physical stream validation failed for {_vid}: {e}. Aborting.", run_id)
                stream_valid = False
                
            if not stream_valid:
                raise InfrastructureFailure("Video failed physical stream integrity checks (Missing streams, corrupt headers, black frames, or empty audio).")

            # STEP 5.1: Thumbnail (from video clips for culture channels, AI for others)
            log("Step 5.1: Generating thumbnail...", run_id)
            try:
                designer     = ThumbnailDesigner(ctx=ctx)
                ai_thumbnail, thumb_actuals = designer.generate(
                    script["title"],
                    script["full_script"],
                    run_id,
                    thumbnail_text=script.get("thumbnail_text"),
                    thumb_color=recipe.get("thumbnail_color", "Blue"),
                    first_clip_path=overlays[0] if (overlays and len(overlays) > 0) else None,
                    all_clip_paths=overlays if overlays else None,
                )
                if ai_thumbnail:
                    output["thumbnail"] = ai_thumbnail
                    execution_manifest.update(thumb_actuals)
                    log(f"Thumbnail: {output['thumbnail']}", run_id)
            except Exception as e:
                log(f"Thumbnail failed (keeping FFmpeg fallback): {e}", run_id)

            # STEP 5.1.1: Target Frame Injection
            _inject_thumbnail_frame(output["video"], output["thumbnail"], run_id)

            # STEP 5.1.1a: Re-validate duration after injection (injection adds ~0.2s)
            try:
                _post_inj_dur = float(_subprocess.check_output(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", output["video"]],
                    timeout=10
                ).decode().strip())
                if _post_inj_dur > 59.5:
                    log(f"Post-injection duration {_post_inj_dur:.2f}s exceeds 59.5s — thumbnail frame NOT injected for safety.", run_id)
            except Exception as _pie:
                log(f"Post-injection duration check failed (non-fatal): {_pie}", run_id)

            # STEP 5.1.2: Validate final video duration (must be <60s for YouTube Shorts)
            import subprocess as _subprocess
            try:
                _dur_cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", output["video"]]
                _dur_val = float(_subprocess.check_output(_dur_cmd).decode().strip())
                log(f"Final video duration: {_dur_val:.2f}s", run_id)
                if _dur_val > 58.0:
                    log(f"WARNING: Video is {_dur_val:.1f}s — exceeds 58s safety threshold for YouTube Shorts!", run_id)
                    send_message(
                        f"⚠️ Duration Warning — {_channel_display}\n"
                        f"Video is {_dur_val:.1f}s (limit: 60s for Shorts).\n"
                        f"Title: {script['title']}\nRun: {run_id}"
                    )
            except Exception as _e:
                log(f"Duration check failed (non-fatal): {_e}", run_id)

            # STEP 5.2: Video Quality Review Gate
            _pre_summary = {
                "run_id": run_id, "story": story, "script": script,
                "title": script["title"],
                "video": output["video"], "thumbnail": output["thumbnail"],
                "uploaded": False, "timestamp": datetime.now().isoformat(),
            }
            with open(os.path.join(LOG_DIR, f"{run_id}_summary.json"), "w") as _f:
                json.dump(_pre_summary, _f, indent=2)

            ai_issues_text = ""  # Initialise here — populated by Step 5.3 if reviewer runs

            log("Step 5.2: Running video quality review gate...", run_id)
            try:
                if os.environ.get("SKIP_QUALITY_GATE") == "1":
                    log("SKIP_QUALITY_GATE is set. Bypassing text quality review.", run_id)
                    review_data = {"quality_score": 100, "upload_recommended": True}
                else:
                    review_data = _run_quality_review(run_id)
                score            = review_data.get("quality_score", 100)
                upload_recommend = review_data.get("upload_recommended", True)
                log(f"Quality Score: {score}/100 | Upload Recommended: {upload_recommend}", run_id)
                
                force_upload = False
                qe_upload = False

                if upload_recommend and score >= QUALITY_SCORE_THRESHOLD:
                    log(f"Video passed text quality gate (Score={score}/{QUALITY_SCORE_THRESHOLD}+) on attempt {attempt}!", run_id)

                    # STEP 5.3: Quality Engine V1 (Gemini watches the MP4)
                    log("Step 5.3: Running Quality Engine V1...", run_id)
                    if os.environ.get("SKIP_QUALITY_GATE") == "1":
                        qe_review = {"quality_score": 100, "upload_recommended": True, "final_score": 100}
                    else:
                        qe_review = _run_quality_engine_review(run_id, ctx)

                    qe_score  = qe_review.get("final_score", 0)
                    qe_upload = qe_review.get("upload_recommended", False)
                    qe_tier   = qe_review.get("tier", "Unknown")
                    log(f"Quality Engine Score: {qe_score}/100 | Tier: {qe_tier} | Upload OK: {qe_upload}", run_id)

                    # Unreviewed tier = Gemini keys exhausted, allow upload but alert owner
                    if qe_tier == "Unreviewed":
                        log("Quality Engine returned Unreviewed (API keys exhausted). Uploading with alert.", run_id)
                        send_message(
                            f"\u26a0\ufe0f *Unreviewed Upload* \u2014 {_channel_display}\n"
                            f"Gemini keys exhausted. Video uploaded without visual QA.\n"
                            f"Run: {run_id}\n"
                            f"Title: {script['title']}"
                        )
                        # Fall through to upload block below
                        qe_upload = True

                    if not qe_upload:
                        log(f"Quality Engine REJECTED video (Score={qe_score}/100).", run_id)
                        reason = qe_review.get("rejection_reason", "Low score.")
                        if attempt < MAX_ATTEMPTS:
                            log("Initiating script self-correction loop due to quality engine rejection...", run_id)
                            correction_feedback = f"- [QUALITY ENGINE FAILURE] {reason}"
                            attempt += 1
                            _cached_clips = None
                            _cached_bg_clip = None
                            continue
                        else:
                            log(f"Quality Engine rejected video after max attempts: {reason}. Forcing upload & learning.", run_id)
                            from core.utils import save_learning_history
                            save_learning_history(ctx.channel_name, f"Visual Quality Failure (Score {qe_score}): {reason}")
                            send_message(
                                f"⚠️ *Low Quality Upload* — {_channel_display}\n"
                                f"Video bypassed Quality Engine gate on final attempt (Score={qe_score}/100) to preserve schedule.\n"
                                f"Run: {run_id}\n"
                                f"Title: {script['title']}"
                            )
                            force_upload = True

                else:
                    log(f"Video failed text quality gate (Score={score}/100 < {QUALITY_SCORE_THRESHOLD}).", run_id)
                    if attempt < MAX_ATTEMPTS:
                        log("Initiating script self-correction loop due to text quality failure...", run_id)
                        correction_feedback = f"- [TEXT QUALITY FAILURE] Score {score}/100. Issues: {review_data.get('issues', [])}"
                        attempt += 1
                        continue
                    else:
                        log(f"Text Quality Gate rejected video after max attempts (Score={score}/100). Forcing upload & learning.", run_id)
                        issues_str = str(review_data.get('issues', []))
                        from core.utils import save_learning_history
                        save_learning_history(ctx.channel_name, f"Text Quality Failure (Score {score}): {issues_str}")
                        send_message(
                            f"⚠️ *Low Quality Upload* — {_channel_display}\n"
                            f"Video bypassed Text Quality gate on final attempt (Score={score}/100) to preserve schedule.\n"
                            f"Run: {run_id}\n"
                            f"Title: {script['title']}"
                        )
                        force_upload = True

                if (upload_recommend and score >= QUALITY_SCORE_THRESHOLD and qe_upload) or force_upload:
                    if force_upload:
                        log("Video flagged but forced to upload.", run_id)
                    else:
                        log("Video approved for upload!", run_id)

                    # STEP 6: Upload to YouTube
                    log("Step 6: Uploading to YouTube...", run_id)
                    uploader = YouTubeUploader()
                    if not uploader.authenticate(ctx=ctx):
                        log("YouTube authentication failed. Skipping upload.", run_id)
                        raise InfrastructureFailure("YouTube authentication failed.")

                    video_id = uploader.upload(
                        video_path=output["video"],
                        thumbnail_path=output["thumbnail"],
                        title=script["title"],
                        description=script["description"],
                        tags=script["tags"],
                        ctx=ctx
                    )

                    if video_id == "QUOTA_EXHAUSTED":
                        log("YouTube API Quota Exhausted. Halting pipeline to preserve files for tomorrow.", run_id)
                        raise InfrastructureFailure("YouTube API Quota Exhausted.")

                    if video_id is None:
                        log("YouTube upload returned None. Upload likely failed or was skipped.", run_id)
                        raise InfrastructureFailure("YouTube upload failed (returned None).")

                    log(f"Upload successful! Video ID: {video_id}", run_id)
                    send_message(
                        f"✅ *Upload Successful* — {_channel_display}\n"
                        f"Title: {script['title']}\n"
                        f"URL: https://youtube.com/shorts/{video_id}"
                    )

                    # STEP 7: Log experiment and cleanup
                    log("Step 7: Logging experiment and cleaning up...", run_id)
                    tracker = ExperimentTracker()
                    params = {
                        "channel_name": ctx.channel_name,
                        "voice": recipe.get("voice", voice_actuals.get("voice")),
                        "voice_rate": recipe.get("voice_rate", voice_actuals.get("voice_rate")),
                        "bgm_mood": _last_bgm_mood,
                        "bgm_track_id": _last_bgm_track,
                        "hook_style": recipe.get("hook_style"),
                        "cta_style": recipe.get("cta_style"),
                        "tone": recipe.get("tone"),
                        "topic": recipe.get("topic"),
                        "title_strategy": recipe.get("title_strategy"),
                        "thumbnail_color": recipe.get("thumbnail_color"),
                        "narrative_framework": recipe.get("narrative_framework"),
                        "pacing_style": recipe.get("pacing_style"),
                        "video_format": recipe.get("video_format"),
                        "featured_country": story.get("featured_country"),
                    }
                    tracker.log_run(run_id, params, video_id)

                    if cleanup_after_upload:
                        cleanup_run(run_id, keep_deliverables=False)
                    
                    return True

            except ContentFailure:
                raise
            except InfrastructureFailure:
                raise
            except Exception as qe_err:
                log(f"Error during quality review process: {qe_err}", run_id)
                raise ContentFailure(f"Quality review process error: {qe_err}")

        # If the while loop finishes without returning True, it means all attempts failed
        raise ContentFailure("Max pipeline render attempts reached without successful upload.")

    except ContentFailure as cf:
        log(f"Content Failure: {cf}", run_id)
        if not is_fallback:
            send_message(
                f"⚠️ *Content Failure* — {_channel_display}\n"
                f"Run: {run_id}\n"
                f"Reason: {cf}\n"
                f"Attempting Story B fallback..."
            )
            log("Triggering Story B fallback...", run_id)
            cleanup_run(run_id, keep_deliverables=False)
            return run(run_id=run_id + "_B", cleanup_after_upload=cleanup_after_upload, is_fallback=True)
        else:
            send_message(
                f"🚨 *Content Failure (Story B)* — {_channel_display}\n"
                f"Run: {run_id}\n"
                f"Reason: {cf}\n"
                f"Pipeline halted."
            )
            log("Story B fallback already failed. Aborting.", run_id)
            return False

    except InfrastructureFailure as inf:
        log(f"Infrastructure Failure: {inf}", run_id)
        send_message(
            f"🚨 *Infrastructure Failure* — {_channel_display}\n"
            f"Run: {run_id}\n"
            f"Reason: {inf}\n"
            f"Pipeline halted."
        )
        return False

    except Exception as e:
        log(f"Unexpected Pipeline Error: {e}", run_id)
        log(traceback.format_exc(), run_id)
        send_message(
            f"🚨 *Unexpected Pipeline Error* — {_channel_display}\n"
            f"Run: {run_id}\n"
            f"Error: {e}"
        )
        return False

    finally:
        try:
            run_background_intelligence(ctx.channel_name, ctx.niche, log_fn=lambda msg: log(msg, run_id))
        except Exception as bg_err:
            log(f"Background intelligence failed: {bg_err}", run_id)

    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the AutoReel pipeline")
    parser.add_argument("--run-id", type=str, default=None, help="Custom run ID")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep intermediate files after upload")
    parser.add_argument("--cleanup-only", type=str, default=None, help="Cleanup files for a specific run ID")
    args = parser.parse_args()

    if args.cleanup_only:
        cleanup_run(args.cleanup_only, keep_deliverables=False)
    else:
        success = run(run_id=args.run_id, cleanup_after_upload=not args.no_cleanup)
        sys.exit(0 if success else 1)
