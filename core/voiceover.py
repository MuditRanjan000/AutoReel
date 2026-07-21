"""
voiceover.py
Converts script text to MP3 voiceover using a 3-tier TTS engine:

  Priority 1 — ElevenLabs (premium, key-based, best quality)
  Priority 2 — Google Cloud TTS (requires GCP credentials JSON + credit card)
  Priority 3 — Microsoft Edge TTS (100% free, no key, no account, no credit card)

The engine is selected automatically based on the voice name:
  - ElevenLabs ID   : alphanumeric string, no hyphens, len > 10 (e.g. "21m00Tcm4TlvDq8ikWAM")
  - Edge TTS voice  : contains "Neural" suffix (e.g. "en-US-GuyNeural")
  - Google Cloud TTS: everything else (e.g. "en-US-Journey-D", "en-US-Neural2-A")

Voice settings are managed in channel JSON files and can be automatically
updated by execution/auto_tune.py based on video retention performance.
"""

import asyncio
import subprocess
import os
import re
import json
import hashlib
import time
import requests
from config.settings import OUTPUT_DIR, VOICE_NAME, VOICE_RATE, ELEVENLABS_API_KEYS
from core.ass_generator import generate_ass

# Try Google Cloud TTS import — only fails if gcp credentials are missing/unconfigured
try:
    from google.cloud import texttospeech as _gcp_tts
    _GCP_AVAILABLE = True
except ImportError:
    _GCP_AVAILABLE = False

_elevenlabs_key_idx = None

# ── Edge TTS voice defaults (free, no credentials) ───────────────────────────
# Maps Google TTS voice names → closest Edge TTS equivalent for seamless fallback
EDGE_TTS_VOICE_MAP = {
    "en-US-Journey-D":    "en-US-GuyNeural",      # Deep male narrator
    "en-US-Journey-F":    "en-US-JennyNeural",    # Female narrator
    "en-US-Neural2-D":    "en-US-GuyNeural",
    "en-US-Neural2-F":    "en-US-JennyNeural",
    "en-GB-Neural2-B":    "en-GB-RyanNeural",
    "en-GB-Neural2-F":    "en-GB-SoniaNeural",
    "en-AU-Neural2-B":    "en-AU-WilliamNeural",
}

# Default free voice for users with no credentials at all
DEFAULT_EDGE_VOICE = "en-US-GuyNeural"

# Edge TTS voice → speaking rate map (edge-tts uses "+10%" style rate strings)
EDGE_TTS_RATES = {
    "+0%":   "+0%",
    "+5%":   "+5%",
    "+8%":   "+8%",
    "+10%":  "+10%",
    "+15%":  "+15%",
    "-5%":   "-5%",
    "-10%":  "-10%",
}


class VoiceoverGenerator:

    def __init__(self, ctx=None):
        self.ctx = ctx
        self.workspace_dir = ctx.workspace_dir if (ctx is not None and hasattr(ctx, 'workspace_dir')) else OUTPUT_DIR
        os.makedirs(self.workspace_dir, exist_ok=True)

    # ── ElevenLabs ────────────────────────────────────────────────────────────
    def _call_elevenlabs_with_rotation(self, text: str, voice_id: str, raw_wav: str):
        global _elevenlabs_key_idx
        num_keys = len(ELEVENLABS_API_KEYS)
        if num_keys == 0:
            raise RuntimeError("No ElevenLabs API keys configured.")
            
        if _elevenlabs_key_idx is None:
            import random
            _elevenlabs_key_idx = random.randint(0, num_keys - 1)

        last_error = None
        for offset in range(num_keys):
            current_idx = (_elevenlabs_key_idx + offset) % num_keys
            api_key = ELEVENLABS_API_KEYS[current_idx]
            print(f"[ElevenLabs] Attempting generation with key {current_idx+1}/{num_keys}...")
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": api_key,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            data = {
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            }
            try:
                time.sleep(1) # Slight delay to avoid rapid-fire spikes
                response = requests.post(url, json=data, headers=headers, timeout=60.0)
                if response.status_code == 200:
                    with open(raw_wav, "wb") as f:
                        for chunk in response.iter_content(chunk_size=1024):
                            if chunk:
                                f.write(chunk)
                    # On success, advance the global index for the next call
                    _elevenlabs_key_idx = (current_idx + 1) % num_keys
                    return True
                elif response.status_code in [401, 429]:
                    print(f"[ElevenLabs] Key {current_idx+1} exhausted or invalid (HTTP {response.status_code}). Rotating...")
                    last_error = f"HTTP {response.status_code}: {response.text}"
                    continue
                else:
                    print(f"[ElevenLabs] Unexpected error HTTP {response.status_code}. Retrying next key...")
                    last_error = f"HTTP {response.status_code}: {response.text}"
                    continue
            except Exception as e:
                print(f"[ElevenLabs] Request failed: {e}")
                last_error = str(e)
                continue
        raise RuntimeError(f"All ElevenLabs keys exhausted or failed. Last error: {last_error}")

    # ── Microsoft Edge TTS (free, no credentials) ─────────────────────────────
    def _call_edge_tts(self, text: str, voice: str, rate: str, raw_wav: str):
        """
        Generate audio using Microsoft Edge TTS via the edge-tts library.
        Completely free — no API key, no account, no credit card required.
        Uses the same neural voices as Azure Cognitive Services.

        Args:
            text:    Plain text (SSML will be stripped before calling this).
            voice:   Edge TTS voice name e.g. "en-US-GuyNeural".
            rate:    Speaking rate string e.g. "+10%" or "-5%".
            raw_wav: Output path for the WAV/MP3 file.
        """
        import edge_tts

        # Strip any residual SSML tags — edge-tts uses its own SSML
        plain_text = re.sub(r'<[^>]+>', '', text).strip()
        plain_text = plain_text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        if not plain_text:
            raise ValueError("[EdgeTTS] Text became empty after SSML stripping.")

        # Resolve voice name: if a GCP voice was passed, map to nearest Edge voice
        if "Neural" not in voice and "Journey" not in voice:
            edge_voice = DEFAULT_EDGE_VOICE
        else:
            edge_voice = EDGE_TTS_VOICE_MAP.get(voice, voice if "Neural" in voice else DEFAULT_EDGE_VOICE)

        # Validate rate string format
        rate_str = rate if re.match(r'^[+-]\d+%$', rate) else "+0%"

        print(f"[EdgeTTS] Generating | Voice={edge_voice} | Rate={rate_str} | Chars={len(plain_text)}")

        async def _run():
            communicate = edge_tts.Communicate(plain_text, edge_voice, rate=rate_str)
            await communicate.save(raw_wav)

        # Run async event loop (works on both Windows and Linux)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Event loop closed")
            loop.run_until_complete(_run())
        except RuntimeError:
            asyncio.run(_run())

        if not os.path.exists(raw_wav) or os.path.getsize(raw_wav) < 1000:
            raise RuntimeError(f"[EdgeTTS] Output file missing or too small: {raw_wav}")

        print(f"[EdgeTTS] Audio generated successfully ({os.path.getsize(raw_wav)//1024} KB)")

    # ── Main generate() ───────────────────────────────────────────────────────
    def generate(self, script, filename: str, recipe: dict = None, ctx=None):
        """
        Generate MP3 voiceover from script text + animated .ass subtitle file.

        TTS priority:
          1. ElevenLabs (if voice_name looks like an ElevenLabs ID)
          2. Google Cloud TTS (if gcp-credentials.json exists)
          3. Microsoft Edge TTS (always available, no credentials needed)
        """
        if ctx is not None:
            self.ctx = ctx
            self.workspace_dir = ctx.workspace_dir if hasattr(ctx, 'workspace_dir') else OUTPUT_DIR
            os.makedirs(self.workspace_dir, exist_ok=True)

        output_path = os.path.join(self.workspace_dir, filename)
        ass_path    = output_path.replace(".mp3", ".ass")
        hash_path   = output_path.replace(".mp3", ".hash")

        # ── Preprocess Script ──────────────────────────────────────────────────
        script_dict = None
        is_ssml = False
        if isinstance(script, dict):
            script_dict = script
            if "ssml_script" in script:
                script_text = script["ssml_script"]
                is_ssml = True
            else:
                script_text = script.get("full_script", "")
        else:
            script_text = script

        script_clean = script_text.strip()
        
        # Strip markdown, asterisks, and weird characters so TTS doesn't read them
        script_clean = re.sub(r'[*_]', '', script_clean)
        
        # Google Cloud TTS Studio voices do not support <emphasis> tags
        script_clean = re.sub(r'</?emphasis[^>]*>', '', script_clean)
        
        # Prevent caching of empty or extremely short failed scripts
        MIN_SCRIPT_LENGTH = 100
        if len(script_clean) < MIN_SCRIPT_LENGTH:
            print(f"[Voiceover] Script is too short ({len(script_clean)} chars). Aborting generation to prevent garbage content.")
            raise ValueError(f"Script is too short ({len(script_clean)} chars) to generate a voiceover. Likely an AI generation failure.")
        
        # ── Caching ────────────────────────────────────────────────────────────
        current_hash = hashlib.md5(script_clean.encode('utf-8')).hexdigest()
        
        recipe = recipe or {}
        voice_default = self.ctx.voice_name if (self.ctx is not None and hasattr(self.ctx, 'voice_name')) else VOICE_NAME
        rate_default  = self.ctx.voice_rate  if (self.ctx is not None and hasattr(self.ctx, 'voice_rate'))  else VOICE_RATE
        pitch_default = self.ctx.voice_pitch if (self.ctx is not None and hasattr(self.ctx, 'voice_pitch')) else 0.0

        self.voice_name  = recipe.get("voice", voice_default)
        self.voice_rate  = recipe.get("voice_rate", rate_default)
        self.voice_pitch = float(recipe.get("voice_pitch", pitch_default))

        if os.path.exists(output_path) and os.path.exists(ass_path) and os.path.exists(hash_path):
            with open(hash_path, "r", encoding="utf-8") as f:
                saved_hash = f.read().strip()
            if saved_hash == current_hash:
                print(f"[Voiceover] CACHE HIT! Script unchanged. Skipping generation for {filename}")
                return output_path, {"voice": self.voice_name, "voice_rate": self.voice_rate}

        # ── Engine Detection ───────────────────────────────────────────────────
        # ElevenLabs: alphanumeric ID string, no hyphens, len > 10
        is_elevenlabs = (
            "-" not in self.voice_name
            and len(self.voice_name) > 10
            and len(ELEVENLABS_API_KEYS) > 0
        )
        # Edge TTS: voice name ends with "Neural"
        is_edge = "Neural" in self.voice_name and not is_elevenlabs
        # Google TTS: everything else (Journey, WaveNet, Standard, Neural2)
        is_gcp = not is_elevenlabs and not is_edge

        raw_wav = output_path.replace(".mp3", "_raw.wav")
        generation_success = False

        # ── Speed / Rate Parsing ───────────────────────────────────────────────
        # [Phase 2 — Fix C1] voice_rate format varies by channel config:
        #   example_crime: "-5%"   (Edge TTS / percentage format)
        #   example_philosophy: "1.0"   (GCP float format, legacy)
        #   ElevenLabs voices: rate is ignored (baked into the voice model)
        speed_ratio = 1.0
        if "Journey" in self.voice_name:
            print(f"[Voiceover] Journey model detected. Locking speed_ratio to 1.0 to preserve natural prosody.")
            if is_ssml:
                print(f"[Voiceover] Journey models do not support SSML. Converting to plain text...")
                script_clean = script_clean.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                script_clean = re.sub(r'<sub alias="([^"]+)">[^<]+</sub>', r'\1', script_clean)
                script_clean = re.sub(r'<break[^>]+>', '... ', script_clean)
                script_clean = re.sub(r'<[^>]+>', '', script_clean)
                is_ssml = False
        else:
            # Try percentage format first: "+10%", "-5%", "0%"
            rate_match = re.match(r"^([+-]?\d+)%", self.voice_rate)
            if rate_match:
                base_rate = int(rate_match.group(1))
                speed_ratio = 1.0 + (base_rate / 100.0)
                speed_ratio = max(0.25, min(4.0, speed_ratio))
            else:
                # Try float format: "1.0", "1.1", "0.9" (legacy GCP format)
                try:
                    float_rate = float(self.voice_rate)
                    if 0.1 <= float_rate <= 4.0:
                        speed_ratio = float_rate
                        pct = int((float_rate - 1.0) * 100)
                        self.voice_rate = f"+{pct}%" if pct >= 0 else f"{pct}%"
                        print(f"[Voiceover] Normalized float voice_rate '{float_rate}' -> '{self.voice_rate}' (speed_ratio={speed_ratio:.2f})")
                except (ValueError, TypeError):
                    print(f"[Voiceover] Unrecognized voice_rate format '{self.voice_rate}', defaulting to 1.0")
                    speed_ratio = 1.0
                    self.voice_rate = "+0%"


        # ── Tier 1: ElevenLabs ────────────────────────────────────────────────
        if is_elevenlabs:
            print(f"[Voiceover] Using ElevenLabs TTS | Voice={self.voice_name}")
            el_text = script_clean.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            el_text = re.sub(r'<[^>]+>', '', el_text)
            try:
                self._call_elevenlabs_with_rotation(el_text, self.voice_name, raw_wav)
                generation_success = True
            except Exception as e:
                print(f"[Voiceover] ElevenLabs failed: {e}")
                print("[Voiceover] Falling back to Edge TTS (free)...")
                is_elevenlabs = False
                is_edge = True
                self.voice_name = DEFAULT_EDGE_VOICE

        # ── Tier 2: Google Cloud TTS ──────────────────────────────────────────
        if is_gcp and not generation_success:
            gcp_creds = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "gcp-credentials.json")
            if not _GCP_AVAILABLE or not os.path.exists(gcp_creds):
                print("[Voiceover] Google Cloud TTS: credentials not found. Falling back to Edge TTS (free)...")
                is_gcp = False
                is_edge = True
                # Map GCP voice → nearest Edge voice
                self.voice_name = EDGE_TTS_VOICE_MAP.get(self.voice_name, DEFAULT_EDGE_VOICE)
            else:
                print(f"[Voiceover] Using Google Cloud TTS | Voice={self.voice_name} | Speed={speed_ratio} | SSML={is_ssml}")
                
                # --- Quota Protection (with file lock for concurrent channel safety) ---
                import datetime
                import sys as _sys
                usage_file = os.path.join(os.path.dirname(__file__), '..', 'config', 'google_tts_usage.json')
                current_month = datetime.datetime.now().strftime("%Y-%m")
                tts_usage = {}
                os.makedirs(os.path.dirname(usage_file), exist_ok=True)
                lock_file = usage_file + ".lock"
                lock_fd = None

                def _lock_file(fd):
                    if _sys.platform == 'win32':
                        import msvcrt
                        msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)

                def _unlock_file(fd):
                    if _sys.platform == 'win32':
                        import msvcrt
                        msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)

                try:
                    lock_fd = open(lock_file, 'w')
                    _lock_file(lock_fd)
                    if os.path.exists(usage_file):
                        try:
                            with open(usage_file, 'r') as f:
                                tts_usage = json.load(f)
                        except Exception:
                            pass
                    current_usage = tts_usage.get(current_month, 0)
                    chars_to_use = len(script_clean)
                    if current_usage + chars_to_use > 950000:
                        raise RuntimeError(
                            f"Google Cloud TTS monthly limit approaching! "
                            f"Usage: {current_usage} + {chars_to_use} > 950000. "
                            f"Falling back to Edge TTS."
                        )
                    tts_usage[current_month] = current_usage + chars_to_use
                    with open(usage_file, 'w') as f:
                        json.dump(tts_usage, f)
                except RuntimeError as quota_err:
                    print(f"[Voiceover] {quota_err}")
                    is_gcp = False
                    is_edge = True
                    self.voice_name = EDGE_TTS_VOICE_MAP.get(self.voice_name, DEFAULT_EDGE_VOICE)
                except Exception as e:
                    print(f"[Voiceover] Warning: TTS quota tracking failed (non-fatal): {e}")
                finally:
                    if lock_fd:
                        try:
                            _unlock_file(lock_fd)
                            lock_fd.close()
                        except Exception:
                            pass
                # --- End Quota Protection ---

                if is_gcp:
                    try:
                        client = _gcp_tts.TextToSpeechClient()
                        synthesis_input = (
                            _gcp_tts.SynthesisInput(ssml=script_clean) if is_ssml
                            else _gcp_tts.SynthesisInput(text=script_clean)
                        )
                        lang_code = "-".join(self.voice_name.split("-")[:2]) if "-" in self.voice_name else "en-US"
                        voice = _gcp_tts.VoiceSelectionParams(language_code=lang_code, name=self.voice_name)
                        kwargs = {
                            "audio_encoding": _gcp_tts.AudioEncoding.LINEAR16,
                            "sample_rate_hertz": 48000,
                            "speaking_rate": speed_ratio,
                        }
                        if "Journey" not in self.voice_name:
                            kwargs["pitch"] = self.voice_pitch
                        audio_config = _gcp_tts.AudioConfig(**kwargs)

                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                response = client.synthesize_speech(
                                    input=synthesis_input, voice=voice, audio_config=audio_config
                                )
                                break
                            except Exception as e:
                                if attempt < max_retries - 1:
                                    print(f"[Voiceover] GCP TTS attempt {attempt+1} failed: {e}. Retrying...")
                                    time.sleep(2)
                                else:
                                    raise RuntimeError(f"GCP TTS failed after {max_retries} attempts: {e}")

                        with open(raw_wav, "wb") as out:
                            out.write(response.audio_content)
                        generation_success = True

                    except Exception as e:
                        print(f"[Voiceover] Google Cloud TTS failed: {e}")
                        print("[Voiceover] Falling back to Edge TTS (free)...")
                        is_gcp = False
                        is_edge = True
                        self.voice_name = EDGE_TTS_VOICE_MAP.get(self.voice_name, DEFAULT_EDGE_VOICE)

        # ── Tier 3: Microsoft Edge TTS (free fallback — always works) ─────────
        if is_edge and not generation_success:
            # Normalize voice name if it came from a GCP/ElevenLabs config
            if "Neural" not in self.voice_name:
                self.voice_name = EDGE_TTS_VOICE_MAP.get(self.voice_name, DEFAULT_EDGE_VOICE)
            try:
                self._call_edge_tts(script_clean, self.voice_name, self.voice_rate, raw_wav)
                generation_success = True
                print(f"[Voiceover] Edge TTS generation complete.")
            except Exception as e:
                raise RuntimeError(
                    f"[Voiceover] ALL TTS engines failed. Last error (Edge TTS): {e}\n"
                    "Check your network connection."
                )

        if not generation_success:
            raise RuntimeError("[Voiceover] No TTS engine produced audio. Check configuration.")

        # ── Post-Processing (same for all engines) ────────────────────────────
        print(f"[Voiceover] Applying ffmpeg podcast mastering to {output_path}...")
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", raw_wav,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=18,highpass=f=80",
            "-c:a", "libmp3lame", "-b:a", "192k",
            output_path
        ]
        subprocess.run(ffmpeg_cmd, capture_output=True, check=True)
        try:
            os.remove(raw_wav)
        except Exception:
            pass

        print(f"[Voiceover] Audio successfully generated: {output_path}")

        # ── Whisper Subtitle Alignment ────────────────────────────────────────
        print(f"[Voiceover] Running Whisper alignment...")
        generate_ass(output_path, ass_path, recipe, script=script_dict or script, ctx=ctx)

        # Save hash on success
        try:
            with open(hash_path, "w", encoding="utf-8") as f:
                f.write(current_hash)
        except Exception as e:
            print(f"[Voiceover] Warning: failed to save hash file: {e}")

        print(f"[Voiceover] Done. Audio -> {output_path} | Subtitles -> {ass_path}")
        return output_path, {"voice": self.voice_name, "voice_rate": self.voice_rate}
