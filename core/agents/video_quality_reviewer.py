"""
core/agents/video_quality_reviewer.py
Implements Quality Engine V1.
"""
import os
import sys
import json
import time
import traceback
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from config.settings import GEMINI_API_KEYS, OUTPUT_DIR, LOG_DIR

class Issue(BaseModel):
    category: str = Field(description="The category this issue belongs to (e.g. Visual Quality)")
    description: str = Field(description="Detailed description of the problem")
    deduction: int = Field(description="Points deducted for this issue")

class QualityScorecard(BaseModel):
    hook_score: int = Field(description="0-100 score for Hook Quality")
    visual_score: int = Field(description="0-100 score for Visual Quality")
    broll_score: int = Field(description="0-100 score for B-roll Relevance")
    retention_score: int = Field(description="0-100 score for Retention Potential")
    story_score: int = Field(description="0-100 score for Story Quality")
    emotion_score: int = Field(description="0-100 score for Emotional Impact")
    subtitle_score: int = Field(description="0-100 score for Subtitle Quality")
    cta_score: int = Field(description="0-100 score for CTA Quality")
    issues: List[Issue] = Field(description="List of specific deductions and reasons")
    summary: str = Field(description="Overall summary of the video's quality")


class VideoQualityReviewer:
    def __init__(self, ctx=None):
        self.ctx = ctx
        self.channel = ctx.channel_name if ctx else "unknown"

    def _upload_video(self, video_path: str, api_key: str):
        from google import genai
        client = genai.Client(api_key=api_key)
        uploaded = client.files.upload(
            file=video_path,
            config={"mime_type": "video/mp4", "display_name": os.path.basename(video_path)},
        )
        max_wait = 120
        waited = 0
        while uploaded.state.name == "PROCESSING":
            if waited >= max_wait:
                raise TimeoutError("Gemini file processing timed out")
            time.sleep(5)
            waited += 5
            uploaded = client.files.get(name=uploaded.name)
        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(f"Gemini file state: {uploaded.state.name}")
        return uploaded.uri, uploaded.name, client

    def _delete_file(self, client, file_name: str):
        try:
            client.files.delete(name=file_name)
        except Exception:
            pass

    def _calculate_weighted_score(self, scorecard: dict) -> float:
        # Based on QUALITY_ENGINE_V1.md weights
        weights = {
            "hook_score": 0.20,
            "visual_score": 0.10,
            "broll_score": 0.10,
            "retention_score": 0.15,
            "story_score": 0.15,
            "emotion_score": 0.10,
            "subtitle_score": 0.10,
            "cta_score": 0.10
        }
        total = 0.0
        for key, weight in weights.items():
            total += scorecard.get(key, 0) * weight
        return total

    def evaluate(self, run_id: str, video_path: str) -> dict:
        if not GEMINI_API_KEYS:
            print("[QualityEngine] No Gemini keys configured. Returning Unreviewed.")
            return {
                "hook_score": None, "visual_score": None, "broll_score": None,
                "retention_score": None, "story_score": None, "emotion_score": None,
                "subtitle_score": None, "cta_score": None,
                "issues": [], "summary": "No Gemini API keys configured. Video not reviewed.",
                "final_score": None, "tier": "Unreviewed",
                "upload_recommended": False, "rejection_reason": "Skipped: no Gemini API keys configured — video cannot be reviewed.",
                "review_source": "skipped_no_keys", "skipped": True,
            }

        # Load metadata
        metadata_path = os.path.join(LOG_DIR, f"{run_id}_summary.json")
        script_text = ""
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r") as f:
                    md = json.load(f)
                    script_text = md.get("script", {}).get("full_script", "")
            except Exception:
                pass

        system_prompt = """You are the Quality Engine V1 for AutoReel.
Your job is to act as a strict gatekeeper.
Evaluate the video on 8 categories (0-100 each).
1. Hook Quality (20%): Does the first 3 seconds present a high-stakes question or intense emotion?
2. Visual Quality (10%): Are there any blurry or watermarked clips?
3. B-roll Relevance (10%): Do visuals accurately reflect spoken text?
4. Retention Potential (15%): Is the visual cadence varied?
5. Story Quality (15%): Clear beginning, middle, end?
6. Emotional Impact (10%): Evokes Outrage, FOMO, Pride, Awe?
7. Subtitle Quality (10%): Clearly readable, safe zones respected?
8. CTA Quality (10%): Natural and conversational?

If there are issues, log them in the `issues` array with deductions.
Return strictly valid JSON matching the schema.
"""
        user_prompt = f"Watch this video and read its script below. Score it strictly.\n\nSCRIPT:\n{script_text}"

        from google.genai import types as genai_types

        max_global_retries = 3
        for global_attempt in range(max_global_retries):
            for key_idx, api_key in enumerate(GEMINI_API_KEYS):
                client = None
                file_name = None
                try:
                    print(f"[QualityEngine] Uploading {video_path}...")
                    file_uri, file_name, client = self._upload_video(video_path, api_key)
                    print(f"[QualityEngine] Scoring video with Gemini...")
                    
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            response = client.models.generate_content(
                                model="gemini-2.5-flash",
                                contents=[
                                    genai_types.Content(
                                        parts=[
                                            genai_types.Part(file_data=genai_types.FileData(file_uri=file_uri, mime_type="video/mp4")),
                                            genai_types.Part(text=user_prompt),
                                        ]
                                    )
                                ],
                                config=genai_types.GenerateContentConfig(
                                    system_instruction=system_prompt,
                                    temperature=0.2,
                                    response_mime_type="application/json",
                                    response_schema=QualityScorecard,
                                ),
                            )
                            break # Success, break out of retry loop
                        except Exception as generate_exc:
                            if "503" in str(generate_exc) and attempt < max_retries - 1:
                                print(f"[QualityEngine] 503 Unavailable. Retrying in 15s... (Attempt {attempt+1}/{max_retries})")
                                time.sleep(15)
                            else:
                                raise generate_exc
                    
                    raw = response.text.strip()
                    if raw.startswith("```"):
                        raw = raw.split("```")[1]
                        if raw.startswith("json"):
                            raw = raw[4:]
                        raw = raw.strip()
                        if raw.endswith("```"):
                            raw = raw[:-3].strip()
                            
                    scorecard = json.loads(raw)
                    
                    # Calculate final weighted score
                    final_score = int(self._calculate_weighted_score(scorecard))
                    scorecard["final_score"] = final_score
                    
                    if final_score >= 90:
                        tier = "Prime"
                        upload_ok = True
                    elif final_score >= 75:
                        tier = "Standard"
                        upload_ok = True
                    else:
                        tier = "Reject"
                        upload_ok = False
                        
                    scorecard["tier"] = tier
                    scorecard["upload_recommended"] = upload_ok
                    scorecard["rejection_reason"] = "Score below 75" if not upload_ok else ""
                    
                    # Clean up
                    self._delete_file(client, file_name)
                    
                    # Log to DB
                    self._log_to_db(run_id, scorecard)
                    
                    return scorecard
                    
                except Exception as e:
                    if client and file_name:
                        self._delete_file(client, file_name)
                    err_str = str(e)
                    if "429" in err_str or "quota" in err_str.lower() or "RESOURCE_EXHAUSTED" in err_str:
                        print(f"[QualityEngine] Key exhausted/rate-limited, rotating... (sleeping 5s)")
                        time.sleep(5)
                        continue
                    else:
                        print(f"[QualityEngine] Error: {e}")
                        traceback.print_exc()
                        with open(os.path.join(LOG_DIR, "quality_engine_error.log"), "a") as f:
                            f.write(f"{datetime.now().isoformat()} - Error: {e}\n{traceback.format_exc()}\n")
                        print("[QualityEngine] Non-quota API error. Returning Unreviewed tier.")
                        unreviewed = {
                            "hook_score": None, "visual_score": None, "broll_score": None,
                            "retention_score": None, "story_score": None, "emotion_score": None,
                            "subtitle_score": None, "cta_score": None,
                            "issues": [], "summary": f"Gemini API error: {str(e)[:120]}. Video not reviewed.",
                            "final_score": None, "tier": "Unreviewed",
                            "upload_recommended": False, "rejection_reason": f"Skipped: Gemini API error — {str(e)[:80]}. Cannot review this video.",
                            "review_source": "skipped_api_error", "skipped": True,
                        }
                        try:
                            self._log_to_db(run_id, unreviewed)
                        except Exception:
                            pass
                        return unreviewed

            if global_attempt < max_global_retries - 1:
                wait_secs = 30 * (2 ** global_attempt)
                print(f"[QualityEngine] All Gemini keys hit quota limits. Waiting {wait_secs}s before global retry {global_attempt+2}/{max_global_retries}...")
                time.sleep(wait_secs)

        print("[QualityEngine] All Gemini keys exhausted after all retries. Returning Unreviewed tier.")
        unreviewed = {
            "hook_score": None, "visual_score": None, "broll_score": None,
            "retention_score": None, "story_score": None, "emotion_score": None,
            "subtitle_score": None, "cta_score": None,
            "issues": [], "summary": "All Gemini API keys exhausted. Video not reviewed.",
            "final_score": None, "tier": "Unreviewed",
            "upload_recommended": False, "rejection_reason": "Skipped: all Gemini API keys exhausted — video cannot be reviewed.",
            "review_source": "skipped_api_exhaustion", "skipped": True,
        }
        try:
            self._log_to_db(run_id, unreviewed)
        except Exception:
            pass
        return unreviewed

    def _log_to_db(self, run_id: str, scorecard: dict):
        try:
            from core.db import get_connection
            conn = get_connection()
            conn.execute("""
                INSERT INTO quality_engine_reviews (
                    run_id, channel, hook_score, visual_score, broll_score,
                    retention_score, story_score, emotion_score, subtitle_score,
                    cta_score, final_score, tier, upload_recommended, rejection_reason,
                    scorecard_json, review_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, self.channel,
                scorecard.get("hook_score"), scorecard.get("visual_score"), scorecard.get("broll_score"),
                scorecard.get("retention_score"), scorecard.get("story_score"), scorecard.get("emotion_score"),
                scorecard.get("subtitle_score"), scorecard.get("cta_score"),
                scorecard.get("final_score"), scorecard.get("tier"),
                1 if scorecard.get("upload_recommended") else 0,
                scorecard.get("rejection_reason", ""),
                json.dumps(scorecard),
                scorecard.get("review_source", "gemini")
            ))
            conn.commit()
            conn.close()
            tier = scorecard.get('tier', 'Unknown')
            score = scorecard.get('final_score')
            score_str = f"{score}/100" if score is not None else "N/A (Unreviewed)"
            print(f"[QualityEngine] Logged scorecard to DB. Score: {score_str} | Tier: {tier}")
        except Exception as e:
            print(f"[QualityEngine] Failed to log to DB: {e}")
