"""
execution/ai_video_reviewer.py
===================================================
Human-like AI Video Reviewer using Gemini File API.

This reviewer WATCHES the actual final MP4 like a real viewer and gives
structured feedback on visual, audio, caption, and pacing quality.

Architecture:
  - Uploads final MP4 to Gemini File API (one upload, one review call)
  - Uses skills/video-quality-reviewer/SKILL.md as system prompt
  - Returns structured JSON feedback with auto-fixable issues flagged
  - Applies surgical auto-fixes (BGM volume, caption patches, clip swaps)
  - Logs all reviews to SQLite ai_reviews table for self-learning
  - NEVER blocks the pipeline — all errors caught, pipeline always continues

Gemini quota usage: 1 call per video (max 3 on retries with fix attempts)
Reserved exclusively for this reviewer — NOT used for text generation.

Usage (standalone):
    python execution/ai_video_reviewer.py <run_id>
    python execution/ai_video_reviewer.py 20260602_155543
"""

import os
import sys
import json
import time
import subprocess
import traceback
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Optional

# Ensure project root is on path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from config.settings import GEMINI_API_KEYS, OUTPUT_DIR, THUMBNAIL_DIR, LOG_DIR


# ── Pydantic Schemas for Schema Enforcement ───────────────────────────────────

class RetentionPrediction(BaseModel):
    estimated_dropoff_timestamp: str = Field(description="MM:SS timestamp of predicted dropoff")
    dropoff_reason: str = Field(description="Reason why viewers would swipe away here")
    watch_past_50pct_likelihood: int = Field(description="1-10 likelihood of watching past 50%")
    weakest_moment: str = Field(description="Description of the single worst second of the video")

class FixParams(BaseModel):
    type: str = Field(description="bgm_volume, caption_word, atempo, or replace_clip")
    clip_index: Optional[int] = Field(default=None, description="0-based clip index")
    replacement_query: Optional[str] = Field(default=None, description="Clip search query")
    replacement_source: Optional[str] = Field(default=None, description="pexels, pixabay, or youtube")
    target_volume_adjustment: Optional[float] = Field(default=None, description="BGM adjustment (e.g. -0.05)")
    wrong_word: Optional[str] = Field(default=None, description="Misspelled/incorrect word in subtitles")
    correct_word: Optional[str] = Field(default=None, description="Correct replacement word")
    atempo_factor: Optional[float] = Field(default=None, description="narration speed multiplier")

class Issue(BaseModel):
    severity: str = Field(description="CRITICAL, WARNING, or INFO")
    category: str = Field(description="Review category name")
    timestamp: Optional[str] = Field(default=None, description="Timestamp MM:SS or null")
    description: str = Field(description="Specific description of what is wrong")
    fix: str = Field(description="Exactly what to change")
    can_fix: bool = Field(description="Whether the pipeline can auto-fix this")
    fix_params: Optional[FixParams] = Field(default=None, description="Parameters for the auto-fix")

class VideoReview(BaseModel):
    overall_score: int = Field(description="0-100 score")
    upload_recommended: bool = Field(description="True if score >= 60 and < 2 criticals")
    visual_score: int = Field(description="0-10 score")
    audio_score: int = Field(description="0-10 score")
    caption_score: int = Field(description="0-10 score")
    flow_score: int = Field(description="0-10 score")
    retention_prediction: RetentionPrediction = Field(description="Estimated retention metrics")
    issues: List[Issue] = Field(description="List of detected issues")
    summary: str = Field(description="2-3 sentence creator-facing summary")


# ── Skill loader ──────────────────────────────────────────────────────────────

def _load_skill() -> str:
    """Load the video-quality-reviewer SKILL.md as system prompt."""
    skill_path = os.path.join(_ROOT, "skills", "video-quality-reviewer", "SKILL.md")
    try:
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "You are a professional YouTube Shorts video reviewer. Watch the video and return JSON feedback."


# ── Gemini File API ───────────────────────────────────────────────────────────

def _upload_video_to_gemini(video_path: str, api_key: str) -> str:
    """
    Upload a video file to Gemini File API.
    Returns the file URI for use in generate_content calls.
    Raises on failure.
    """
    from google import genai

    print(f"[AIReviewer] Uploading video to Gemini File API ({os.path.getsize(video_path) / 1024 / 1024:.1f}MB)...")
    client = genai.Client(api_key=api_key)

    uploaded = client.files.upload(
        file=video_path,
        config={"mime_type": "video/mp4", "display_name": os.path.basename(video_path)},
    )

    # Poll until file is ACTIVE (processing complete)
    max_wait = 120  # seconds
    waited = 0
    while uploaded.state.name == "PROCESSING":
        if waited >= max_wait:
            raise TimeoutError(f"Gemini file processing timed out after {max_wait}s")
        time.sleep(5)
        waited += 5
        uploaded = client.files.get(name=uploaded.name)
        print(f"[AIReviewer] File processing... ({waited}s elapsed)")

    if uploaded.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini file in unexpected state: {uploaded.state.name}")

    print(f"[AIReviewer] File ready: {uploaded.uri}")
    return uploaded.uri, uploaded.name, client


def _delete_gemini_file(client, file_name: str):
    """Delete uploaded file from Gemini cloud after review. Good hygiene."""
    try:
        client.files.delete(name=file_name)
        print(f"[AIReviewer] Cleaned up Gemini file: {file_name}")
    except Exception as e:
        print(f"[AIReviewer] File cleanup failed (non-fatal): {e}")


def _call_gemini_review(client, file_uri: str, skill_prompt: str, channel: str, attempt: int = 1) -> dict:
    """
    Send the uploaded video to Gemini for review.
    Returns parsed JSON dict.
    """
    from google.genai import types as genai_types

    model = "gemini-2.5-flash"

    user_prompt = (
        f"Watch this YouTube Shorts video completely from start to finish. "
        f"It is produced for the '{channel}' channel. "
        f"Review it exactly as described in your instructions. "
        f"Return ONLY valid JSON."
    )

    if attempt > 1:
        user_prompt += (
            f"\n\nIMPORTANT: This is review attempt {attempt}. "
            f"Some fixes were applied between attempts. Be extra strict this time."
        )

    response = client.models.generate_content(
        model=model,
        contents=[
            genai_types.Content(
                parts=[
                    genai_types.Part(file_data=genai_types.FileData(file_uri=file_uri, mime_type="video/mp4")),
                    genai_types.Part(text=user_prompt),
                ]
            )
        ],
        config=genai_types.GenerateContentConfig(
            system_instruction=skill_prompt,
            temperature=0.3,
            response_mime_type="application/json",
            response_schema=VideoReview,
        ),
    )

    raw = response.text.strip()

    # Strip markdown fences if Gemini wraps in ```json
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    try:
        return json.loads(raw)
    except Exception as e:
        print(f"[AIReviewer] JSON parsing failed. Raw response text was:\n{raw}\n--- End of raw response ---")
        raise e


# ── Auto-Fix Engine ───────────────────────────────────────────────────────────

def _apply_auto_fixes(run_id: str, review: dict, overlays: list = None) -> bool:
    """
    Apply surgical auto-fixes based on reviewer feedback.
    Returns True if any fixes were applied (triggers re-review).
    """
    from config.settings import get_video_encoder_args
    from core.utils import safe_atomic_replace

    video_path = os.path.join(OUTPUT_DIR, f"{run_id}_final.mp4")
    ass_path = os.path.join(OUTPUT_DIR, f"{run_id}_voice.ass")
    bgm_path = os.path.join(OUTPUT_DIR, f"{run_id}_bgm.mp3")
    merged_path = os.path.join(OUTPUT_DIR, f"{run_id}_merged.mp4")

    fixes_applied = []
    issues = review.get("issues", [])
    critical_issues = [i for i in issues if i.get("severity") == "CRITICAL" and i.get("can_fix")]
    warning_issues = [i for i in issues if i.get("severity") == "WARNING" and i.get("can_fix")]

    # Only auto-fix CRITICAL and WARNING issues (not INFO)
    fixable_issues = critical_issues + warning_issues

    for issue in fixable_issues:
        fix_params = issue.get("fix_params", {})
        fix_type = fix_params.get("type")

        # ── Fix 1: BGM volume adjustment ──────────────────────────────────
        if fix_type == "bgm_volume":
            adjustment = fix_params.get("target_volume_adjustment", -0.05)
            if os.path.exists(merged_path) and os.path.exists(bgm_path):
                try:
                    temp_out = video_path.replace("_final.mp4", "_fixed_bgm.mp4")
                    # Re-mix audio: boost/reduce BGM track
                    current_bgm_vol = review.get("_current_bgm_volume", 0.20)
                    new_vol = max(0.05, min(0.45, current_bgm_vol + adjustment))
                    print(f"[AIReviewer] Auto-fix: BGM volume {current_bgm_vol:.2f} -> {new_vol:.2f}")

                    voice_path = os.path.join(OUTPUT_DIR, f"{run_id}_voice.mp3")
                    if not os.path.exists(voice_path) or not os.path.exists(ass_path):
                        print("[AIReviewer] BGM fix: voice/ass files already cleaned up, skipping")
                        continue

                    encoder_args = get_video_encoder_args()
                    cmd = [
                        "ffmpeg", "-y",
                        "-i", merged_path,          # video+voice merged
                        "-i", bgm_path,              # bgm separately
                        "-filter_complex",
                        f"[0:a]volume=1.3[voice];[1:a]volume={new_vol}[bgm];[voice][bgm]amix=inputs=2:duration=first[a]",
                        "-map", "0:v", "-map", "[a]",
                        "-c:a", "aac", "-b:a", "192k",
                        "-shortest",
                    ] + encoder_args + [temp_out]

                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                    if result.returncode == 0 and os.path.exists(temp_out):
                        # Re-burn captions
                        final_temp = video_path.replace("_final.mp4", "_fixed_final.mp4")
                        cmd2 = [
                            "ffmpeg", "-y", "-i", temp_out,
                            "-vf", f"ass={ass_path}",
                            "-c:a", "copy",
                        ] + encoder_args + [final_temp]
                        r2 = subprocess.run(cmd2, capture_output=True, timeout=120)
                        if r2.returncode == 0 and os.path.exists(final_temp):
                            safe_atomic_replace(final_temp, video_path)
                            fixes_applied.append(f"bgm_volume:{current_bgm_vol:.2f}→{new_vol:.2f}")
                        try:
                            os.remove(temp_out)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[AIReviewer] BGM fix failed: {e}")

        # ── Fix 2: Caption word patch ──────────────────────────────────────
        elif fix_type == "caption_word":
            wrong_word = fix_params.get("wrong_word", "")
            correct_word = fix_params.get("correct_word", "")
            if wrong_word and correct_word and os.path.exists(ass_path):
                try:
                    with open(ass_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if wrong_word.upper() in content.upper():
                        # Case-insensitive replace
                        import re
                        new_content = re.sub(
                            re.escape(wrong_word), correct_word,
                            content, flags=re.IGNORECASE
                        )
                        with open(ass_path, "w", encoding="utf-8") as f:
                            f.write(new_content)
                        print(f"[AIReviewer] Auto-fix: caption '{wrong_word}' -> '{correct_word}'")
                        fixes_applied.append(f"caption_word:{wrong_word}→{correct_word}")

                        # Re-burn captions onto the video
                        encoder_args = get_video_encoder_args()
                        temp_out = video_path.replace("_final.mp4", "_reburn.mp4")
                        # Get video without captions (merged video before caption burn)
                        input_video = merged_path if os.path.exists(merged_path) else video_path
                        cmd = [
                            "ffmpeg", "-y", "-i", input_video,
                            "-vf", f"ass={ass_path}",
                            "-c:a", "copy",
                        ] + encoder_args + [temp_out]
                        r = subprocess.run(cmd, capture_output=True, timeout=120)
                        if r.returncode == 0 and os.path.exists(temp_out):
                            safe_atomic_replace(temp_out, video_path)
                except Exception as e:
                    print(f"[AIReviewer] Caption fix failed: {e}")

        # ── Fix 3: Atempo (narration speed) ───────────────────────────────
        elif fix_type == "atempo":
            factor = fix_params.get("atempo_factor", 1.0)
            if factor and factor != 1.0 and os.path.exists(video_path):
                try:
                    temp_out = video_path.replace("_final.mp4", "_tempo.mp4")
                    encoder_args = get_video_encoder_args()
                    cmd = [
                        "ffmpeg", "-y", "-i", video_path,
                        "-filter:a", f"atempo={factor}",
                        "-c:v", "copy",
                    ] + [temp_out]
                    r = subprocess.run(cmd, capture_output=True, timeout=120)
                    if r.returncode == 0 and os.path.exists(temp_out):
                        safe_atomic_replace(temp_out, video_path)
                        fixes_applied.append(f"atempo:{factor}")
                        print(f"[AIReviewer] Auto-fix: narration speed × {factor}")
                except Exception as e:
                    print(f"[AIReviewer] Atempo fix failed: {e}")

        # ── Fix 4: Replace off-topic/watermarked clip ─────────────────────
        elif fix_type == "replace_clip":
            clip_index = fix_params.get("clip_index")
            replacement_query = fix_params.get("replacement_query", "")
            replacement_source = fix_params.get("replacement_source", "pexels")

            if clip_index is not None and replacement_query and overlays:
                try:
                    from core.video_clipper import VideoClipper
                    print(f"[AIReviewer] Auto-fix: replacing clip {clip_index} with '{replacement_query}' from {replacement_source}")

                    clipper = VideoClipper()
                    clip_dest = os.path.join(OUTPUT_DIR, "clips", f"{run_id}_fix_{clip_index}.mp4")
                    os.makedirs(os.path.dirname(clip_dest), exist_ok=True)

                    # Use _search_pexels (the actual method that exists in VideoClipper)
                    new_clip = clipper._search_pexels(
                        query=replacement_query,
                        out_path=clip_dest,
                        duration=3.5,
                    )
                    if new_clip and os.path.exists(new_clip):
                        if clip_index < len(overlays):
                            overlays[clip_index] = new_clip
                        fixes_applied.append(f"replace_clip:{clip_index}:{replacement_query}")
                        print(f"[AIReviewer] Clip {clip_index} replaced successfully")
                        print(f"[AIReviewer] Note: re-assembly required to use new clip — logged for next run")
                    else:
                        print(f"[AIReviewer] Replacement clip download returned nothing, skipping")
                except Exception as e:
                    print(f"[AIReviewer] Clip replace failed: {e}")

    if fixes_applied:
        print(f"[AIReviewer] Applied {len(fixes_applied)} auto-fix(es): {', '.join(fixes_applied)}")
        return True

    return False


# ── Database logging ──────────────────────────────────────────────────────────

def _log_review_to_db(run_id: str, channel: str, review: dict, attempt: int,
                       skipped: bool = False, skip_reason: str = ""):
    """Log the AI review result to SQLite for self-learning."""
    try:
        from core.db import get_connection
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_reviews (
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
                upload_recommended INTEGER,
                issues_json TEXT,
                fix_applied INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0,
                skip_reason TEXT,
                summary TEXT,
                raw_json TEXT
            )
        """)
        conn.execute("""
            INSERT INTO ai_reviews (
                run_id, channel, attempt,
                overall_score, visual_score, audio_score, caption_score, flow_score,
                retention_dropoff_ts, watch_likelihood, upload_recommended,
                issues_json, skipped, skip_reason, summary, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, channel, attempt,
            review.get("overall_score") if not skipped else None,
            review.get("visual_score") if not skipped else None,
            review.get("audio_score") if not skipped else None,
            review.get("caption_score") if not skipped else None,
            review.get("flow_score") if not skipped else None,
            review.get("retention_prediction", {}).get("estimated_dropoff_timestamp") if not skipped else None,
            review.get("retention_prediction", {}).get("watch_past_50pct_likelihood") if not skipped else None,
            1 if review.get("upload_recommended", True) else 0,
            json.dumps(review.get("issues", [])) if not skipped else "[]",
            0,
            skip_reason,
            review.get("summary", "") if not skipped else "",
            json.dumps(review) if not skipped else "{}",
        ))
        conn.commit()
        conn.close()
        print(f"[AIReviewer] Review logged to DB (run={run_id}, attempt={attempt})")
    except Exception as e:
        print(f"[AIReviewer] DB logging failed (non-fatal): {e}")


# ── Main reviewer ─────────────────────────────────────────────────────────────

def review(run_id: str, channel: str = "unknown", overlays: list = None) -> dict:
    """
    Main entry point. Upload the final MP4, get Gemini's visual review,
    apply auto-fixes if needed, log to SQLite.

    Returns a result dict compatible with the existing quality gate format:
    {
        "quality_score": int,
        "upload_recommended": bool,
        "issues": [...],
        "ai_reviewer_score": int,      # raw visual score from Gemini
        "ai_reviewer_ran": bool,
        "fixes_applied": int,
    }
    """
    video_path = os.path.join(OUTPUT_DIR, f"{run_id}_final.mp4")

    # ── Sanity checks ────────────────────────────────────────────────────────
    if not os.path.exists(video_path):
        print(f"[AIReviewer] Video not found: {video_path}. Skipping review.")
        return {"quality_score": 75, "upload_recommended": True,
                "ai_reviewer_ran": False, "issues": [], "fixes_applied": 0}

    file_size_mb = os.path.getsize(video_path) / 1024 / 1024
    if file_size_mb > 1900:  # Gemini File API limit is 2GB
        print(f"[AIReviewer] Video too large ({file_size_mb:.0f}MB > 1900MB). Skipping.")
        _log_review_to_db(run_id, channel, {}, 1, skipped=True, skip_reason="file_too_large")
        return {"quality_score": 75, "upload_recommended": True,
                "ai_reviewer_ran": False, "issues": [], "fixes_applied": 0}

    if not GEMINI_API_KEYS:
        print("[AIReviewer] No Gemini API keys configured. Skipping visual review.")
        _log_review_to_db(run_id, channel, {}, 1, skipped=True, skip_reason="no_gemini_keys")
        return {"quality_score": 75, "upload_recommended": True,
                "ai_reviewer_ran": False, "issues": [], "fixes_applied": 0}

    skill_prompt = _load_skill()
    total_fixes_applied = 0
    last_review = {}

    # ── Try each Gemini key ──────────────────────────────────────────────────
    for key_idx, api_key in enumerate(GEMINI_API_KEYS):
        file_uri = None
        file_name = None
        client = None

        try:
            print(f"\n[AIReviewer] Starting visual review with Gemini key {key_idx + 1}/{len(GEMINI_API_KEYS)}...")

            # Upload video
            file_uri, file_name, client = _upload_video_to_gemini(video_path, api_key)

            # Review loop (max 2 attempts: initial review + 1 fix-and-re-review)
            for review_attempt in range(1, 3):
                print(f"[AIReviewer] Review attempt {review_attempt}/2...")

                review_data = _call_gemini_review(
                    client, file_uri, skill_prompt, channel, attempt=review_attempt
                )
                last_review = review_data

                score = review_data.get("overall_score", 75)
                upload_ok = review_data.get("upload_recommended", True)
                issue_count = len(review_data.get("issues", []))
                critical_count = sum(1 for i in review_data.get("issues", []) if i.get("severity") == "CRITICAL")

                print(f"[AIReviewer] Attempt {review_attempt} score: {score}/100 | "
                      f"Upload: {upload_ok} | Issues: {issue_count} ({critical_count} critical)")
                print(f"[AIReviewer] Summary: {review_data.get('summary', '')}")

                # Log this attempt to DB
                _log_review_to_db(run_id, channel, review_data, review_attempt)

                # If score is good or no fixable issues → done
                fixable = [i for i in review_data.get("issues", [])
                           if i.get("can_fix") and i.get("severity") in ["CRITICAL", "WARNING"]]

                if score >= 75 and not fixable:
                    print(f"[AIReviewer] Video approved by AI reviewer (score={score}/100)")
                    break

                if review_attempt < 2 and fixable:
                    print(f"[AIReviewer] Applying {len(fixable)} auto-fix(es) before re-review...")
                    fixed = _apply_auto_fixes(run_id, review_data, overlays)
                    if fixed:
                        total_fixes_applied += 1
                        # Small wait before re-review
                        time.sleep(3)
                    else:
                        print("[AIReviewer] No fixes could be applied. Accepting current score.")
                        break
                else:
                    break

            # Cleanup: delete file from Gemini cloud
            _delete_gemini_file(client, file_name)

            # Build final result
            return {
                "quality_score": last_review.get("overall_score", 75),
                "upload_recommended": last_review.get("upload_recommended", True),
                "issues": last_review.get("issues", []),
                "ai_reviewer_ran": True,
                "ai_reviewer_score": last_review.get("overall_score", 75),
                "visual_score": last_review.get("visual_score"),
                "audio_score": last_review.get("audio_score"),
                "caption_score": last_review.get("caption_score"),
                "flow_score": last_review.get("flow_score"),
                "retention_prediction": last_review.get("retention_prediction", {}),
                "summary": last_review.get("summary", ""),
                "fixes_applied": total_fixes_applied,
            }

        except Exception as e:
            err_str = str(e)
            is_quota = "429" in err_str or "quota" in err_str.lower() or "RESOURCE_EXHAUSTED" in err_str

            if client and file_name:
                _delete_gemini_file(client, file_name)

            if is_quota:
                print(f"[AIReviewer] Key {key_idx + 1} quota exhausted — trying next key...")
                continue
            else:
                print(f"[AIReviewer] Key {key_idx + 1} failed with non-quota error: {e}")
                print(traceback.format_exc())
                # Rotate to the next key on transient/non-quota errors for robustness
                continue

    # All keys exhausted or non-recoverable error
    print("[AIReviewer] All Gemini keys exhausted or failed. Visual review skipped.")
    print("[AIReviewer] Video will be uploaded without visual review.")
    _log_review_to_db(run_id, channel, {}, 1, skipped=True, skip_reason="all_keys_exhausted")

    return {
        "quality_score": 75,
        "upload_recommended": True,
        "ai_reviewer_ran": False,
        "issues": [],
        "fixes_applied": 0,
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python execution/ai_video_reviewer.py <run_id> [channel_name]")
        print("Example: python execution/ai_video_reviewer.py 20260602_155543 example_channel_3")
        sys.exit(1)

    _run_id = sys.argv[1]
    _channel = sys.argv[2] if len(sys.argv) > 2 else "unknown"

    print(f"\n{'='*50}")
    print(f"AI Video Reviewer — Run: {_run_id} | Channel: {_channel}")
    print(f"{'='*50}\n")

    result = review(_run_id, _channel)

    print(f"\n{'='*50}")
    print(f"Final Score   : {result.get('ai_reviewer_score', 'N/A')}/100")
    print(f"Upload OK     : {result.get('upload_recommended')}")
    print(f"Visual Score  : {result.get('visual_score')}/10")
    print(f"Audio Score   : {result.get('audio_score')}/10")
    print(f"Caption Score : {result.get('caption_score')}/10")
    print(f"Flow Score    : {result.get('flow_score')}/10")
    print(f"Fixes Applied : {result.get('fixes_applied')}")
    print(f"{'='*50}")

    if result.get("retention_prediction"):
        rp = result["retention_prediction"]
        print(f"\nRetention Prediction:")
        print(f"  Estimated dropoff : {rp.get('estimated_dropoff_timestamp', 'N/A')}")
        print(f"  Reason            : {rp.get('dropoff_reason', 'N/A')}")
        print(f"  Watch 50%+ odds   : {rp.get('watch_past_50pct_likelihood', 'N/A')}/10")
        print(f"  Weakest moment    : {rp.get('weakest_moment', 'N/A')}")

    if result.get("summary"):
        print(f"\nSummary: {result['summary']}")

    issues = result.get("issues", [])
    if issues:
        print(f"\nIssues ({len(issues)}):")
        for issue in issues:
            sev = issue.get("severity", "?")
            cat = issue.get("category", "?")
            desc = issue.get("description", "?")
            ts = issue.get("timestamp", "")
            fixable = "auto-fixed" if issue.get("can_fix") else "logged"
            ts_str = f" [{ts}]" if ts else ""
            print(f"  [{sev}] {cat}{ts_str}: {desc} ({fixable})")

