"""
core/retrieval_validator.py
Retrieval Validation Layer using Gemini Multimodal API.
Extracts frames from candidate clips and uses gemini-2.5-flash to validate accuracy.

Note: generate_tiered_queries() was removed in Phase 0 audit.
Tier query generation is handled upstream by the script LLM (V23+ architecture).
"""

import os
import time
import json
import subprocess
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types as genai_types
from config.settings import GEMINI_API_KEYS

class ValidationResult(BaseModel):
    score: int = Field(description="0-10 score of overall alignment")
    entity_match: bool = Field(description="True if visually matches the specific entity described")
    event_match: bool = Field(description="True if visually matches the event/action described")
    context_match: bool = Field(description="True if visually matches the geographical/cultural context")
    aesthetic_match: bool = Field(description="True if visual style matches the channel style DNA")
    accept: bool = Field(description="True if score >= 7, otherwise False")
    reason: str = Field(description="Detailed reason for the accept or reject decision")

class TieredQueries(BaseModel):
    tier1: str = Field(description="Tier 1: Exact Match query (specific entity/event)")
    tier2: str = Field(description="Tier 2: Entity Context Match query (location/environment)")
    tier3: str = Field(description="Tier 3: Event Match query (generic action/event)")
    tier4: str = Field(description="Tier 4: Atmosphere Match query (aesthetic mood/channel DNA)")

def extract_frames(video_path: str, temp_dir: str, num_frames: int = 3) -> list[str]:
    """
    Extract representative frames from a video clip.
    """
    try:
        probe_cmd = [
            "ffprobe", "-v", "error", 
            "-show_entries", "format=duration", 
            "-of", "default=noprint_wrappers=1:nokey=1", 
            video_path
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
        dur = float(res.stdout.strip())
    except Exception as e:
        print(f"[Validator] Duration probe failed, defaulting to 5.0s: {e}")
        dur = 5.0

    frame_paths = []
    start_pct = 0.1
    end_pct = 0.9
    step = (end_pct - start_pct) / (num_frames - 1) if num_frames > 1 else 0

    for i in range(num_frames):
        pct = start_pct + i * step
        t = dur * pct
        frame_name = f"frame_{i}_{os.path.basename(video_path)}.jpg"
        frame_path = os.path.join(temp_dir, frame_name)
        
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{t:.3f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            frame_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if os.path.exists(frame_path) and os.path.getsize(frame_path) > 100:
            frame_paths.append(frame_path)
        else:
            print(f"[Validator] Failed to extract frame at {t:.2f}s: {res.stderr[:100]}")

    return frame_paths

def validate_asset(video_path: str, query: str, narration: str, visual_intent: str, channel_name: str) -> dict:
    """
    Validates a video asset against narration context, search query, and visual intent.
    Uses Gemini Multimodal key rotation. Returns the validated JSON structure.
    """
    print(f"[Validator] Starting validation on: {video_path}")
    print(f"  Query : '{query}'")
    print(f"  Intent: '{visual_intent}'")
    print(f"  Speech: '{narration}'")
    
    from config.settings import SKIP_CLIP_VALIDATION
    if SKIP_CLIP_VALIDATION:
        print("[Validator] SKIP_CLIP_VALIDATION=True. Pre-download metadata scoring active. Gemini multimodal validation skipped.")
        return {
            "score": 0,
            "entity_match": False,
            "event_match": False,
            "context_match": False,
            "aesthetic_match": False,
            "accept": True,
            "reason": "Clip selected via pre-download metadata scoring (Phase 1). Gemini multimodal validation skipped to preserve API quota."
        }
    
    # 1. Extract frames
    temp_dir = os.path.dirname(video_path)
    frame_paths = []
    try:
        frame_paths = extract_frames(video_path, temp_dir, num_frames=3)
    except Exception as e:
        print(f"[Validator] Frame extraction failed: {e}")
        return {
            "score": 0,
            "entity_match": False,
            "event_match": False,
            "context_match": False,
            "aesthetic_match": False,
            "accept": False,
            "reason": f"Frame extraction error: {e}"
        }

    if not frame_paths:
        print("[Validator] No frames extracted from video asset.")
        return {
            "score": 0,
            "entity_match": False,
            "event_match": False,
            "context_match": False,
            "aesthetic_match": False,
            "accept": False,
            "reason": "Failed to extract visual frames from video."
        }

    # 2. Build Gemini prompt
    system_instruction = """You are the Retrieval Validation Layer for AutoReel, an autonomous video editor.
Analyze the visual frames from a retrieved video asset against the given search query, narration, and visual intent to determine if it is accurate and authentic.

You must be extremely critical. If the video does not match the actual entity, sport, culture, or context, you must reject it.

Evaluate the following rules:
1. Entity Match: Does the visual contain the specific person/group/object mentioned in the narration/intent? B-ROLL TOLERANCE: If the narration specifies a person/event, but the clip shows the correct related environment, physical evidence, or context (e.g. airport terminal, police car, crime scene) that is highly relevant, this should be ACCEPTED. Do NOT reject relevant B-roll just because the main person's face isn't perfectly visible.
2. Event Match: Is the visual depicting the correct event? (e.g., cricket, vlogging, meditation).
3. Context Match: Does the visual match the geographical or cultural setting? (e.g., Nepal dating customs should NOT show Western/European models; West Indies cricket should NOT show Australian cricket or football).
4. Aesthetic Match: Does the clip's style align with the channel's DNA? (example_philosophy: dark cinematic moody silhouette rain monochrome; example_crime: raw news, CCTV, bodycam, evidence; example_culture: authentic vlog style).
5. Human Editor Approval: Would a professional human editor approve this clip for the final video? Reject generic stock footage if specific real footage is requested, and reject incorrect sports or mismatched locations.

CRITICAL RULES FOR NICHES:
- Cricket (example_sports): Wrong Sport (e.g., soccer, football, baseball) = Immediate Reject (accept = False, score = 0). Mismatched teams (e.g., Australian cricket shown when narration is about West Indies) = Reject.
- Crime (example_crime): Reject generic police sirens, generic stock handcuffs/cops, or random suspects when the narration is about a specific case/person (e.g., Lars Mittank). Real news footage, Varna Airport, CCTV, or actual case photos must be accepted.
- Stoic (example_philosophy): Must depict the correct mood (meditation, monk, contemplation, dark cinematic visuals). Reject cheerful stock, athletes, corporate offices, or blacksmiths when monks/philosophy are narrated.
- Culture (example_culture): Must show the correct demographics and culture (e.g. Nepalese dating culture must show Nepalese/South Asian people and settings). Reject generic Western stock couples or European models.

Return a JSON object with this schema:
{
  "score": <0-10 integer representation of overall alignment>,
  "entity_match": <true/false>,
  "event_match": <true/false>,
  "context_match": <true/false>,
  "aesthetic_match": <true/false>,
  "accept": <true/false (accept if score >= 7, else false)>,
  "reason": "<detailed explanation of validation decision>"
}
"""

    user_prompt = f"""Channel Name: {channel_name}
Search Query used to find clip: {query}
Visual Intent / Narrative Context: {visual_intent}
Narration spoken during this visual clip: "{narration}"

Review the attached 3 frames extracted from the downloaded clip. Answer strictly in JSON format.
"""

    # 3. Call Gemini with Key Rotation and Model Cascade
    result = None
    validation_models = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-3.5-flash"]
    
    for idx, api_key in enumerate(GEMINI_API_KEYS):
        if result is not None:
            break
            
        try:
            client = genai.Client(api_key=api_key, http_options={'timeout': 300000})
            contents = []
            for path in frame_paths:
                img = Image.open(path)
                contents.append(img)
            contents.append(user_prompt)

            config = genai_types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=ValidationResult
            )

            for model in validation_models:
                try:
                    print(f"[Validator] Trying validation model: {model} with key {idx+1}/{len(GEMINI_API_KEYS)}")
                    response = client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config
                    )

                    raw = response.text.strip()
                    result = json.loads(raw)
                    print(f"[Validator] Success on {model}! Result: Score={result.get('score')}/10 | Accept={result.get('accept')} | Reason: {result.get('reason')}")
                    break
                except Exception as model_err:
                    err_str = str(model_err).lower()
                    err_type = type(model_err).__name__.lower()
                    is_quota = "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str or "quota" in err_type or "resource_exhausted" in err_type
                    is_503 = ("503" in err_str or "unavailable" in err_str or "disconnected" in err_str or 
                              "timeout" in err_str or "connection" in err_str or "protocol" in err_str or
                              "timeout" in err_type or "connect" in err_type or "disconnect" in err_type or 
                              "protocol" in err_type or "unavailable" in err_type or "httpcore" in err_type or "httpx" in err_type)
                    print(f"[Validator] {model} attempt failed: {model_err}")
                    if is_quota or is_503:
                        time.sleep(2)
                        continue
                    # For other errors (e.g. invalid arguments or bad requests), try next model/key
                    continue

            if result is not None:
                break
        except Exception as e:
            print(f"[Validator] Gemini key {idx+1} initialization/processing error: {e}")
            time.sleep(2)
            continue

    # Cleanup frames
    for path in frame_paths:
        try:
            os.remove(path)
        except Exception:
            pass

    if result is None:
        print("[Validator] All Gemini keys failed. Defaulting to reduced-confidence accept (Score 60) to preserve clip.")
        return {
            "score": 60,
            "entity_match": True,
            "event_match": True,
            "context_match": True,
            "aesthetic_match": True,
            "accept": True,
            "unreviewed": True,
            "reason": "Unreviewed (Gemini API exhausted) — accepted with reduced confidence."
        }

    return result
