"""
video_assembler.py
Merges gameplay background, PIP overlays, voiceover, BGM, and captions
into the final YouTube Short.
"""

import subprocess
import os
import json
import shutil
import random
import logging
import textwrap
from typing import Optional
from config.settings import OUTPUT_DIR, THUMBNAIL_DIR, CHANNEL_NAME, get_video_encoder_args
from core.channel_context import ChannelContext
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Cross-platform font resolution ────────────────────────────────────────────
_FONT_CANDIDATES_BOLD = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/verdanab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]

_FONT_CANDIDATES_REGULAR = [
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/verdana.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]

_HUMAN_METADATA_COMMENTS = [
    "Edited on PC", "Quick edit", "Final cut", "v2", "export", "done",
    "upload ready", "finished", "rev1", "master"
]


def _resolve_font(candidates: list) -> Optional[str]:
    """Return the first font path that exists on this OS, or None."""
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


class VideoAssembler:

    def __init__(self, ctx: ChannelContext = None):
        self.ctx = ctx
        self.workspace_dir = ctx.workspace_dir if (ctx is not None and hasattr(ctx, 'workspace_dir')) else OUTPUT_DIR
        self.thumbnail_dir = ctx.thumbnail_dir if (ctx is not None and hasattr(ctx, 'thumbnail_dir')) else THUMBNAIL_DIR
        self.channel_name = ctx.display_name if (ctx is not None and hasattr(ctx, 'display_name')) else CHANNEL_NAME
        self.channel_slug = ctx.channel_name if (ctx is not None and hasattr(ctx, 'channel_name')) else None
        os.makedirs(self.workspace_dir, exist_ok=True)
        os.makedirs(self.thumbnail_dir, exist_ok=True)

    # ----------------------------------------------------------
    def _run_ffmpeg(self, cmd: list, timeout: int = 180) -> subprocess.CompletedProcess:
        """Centralised wrapper for running ffmpeg/ffprobe commands."""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                logger.error(f"Command failed (rc={result.returncode}): {' '.join(cmd[:5])}...")
                logger.error(f"Stderr: {result.stderr[-500:]}")
            return result
        except subprocess.TimeoutExpired:
            logger.error(f"TimeoutExpired after {timeout}s for command: {' '.join(cmd[:5])}...")
            # Return a mock completed process to prevent crashes
            return subprocess.CompletedProcess(cmd, returncode=-1, stderr="TimeoutExpired", stdout="")
        except Exception as e:
            logger.error(f"Exception running command {' '.join(cmd[:5])}: {e}")
            return subprocess.CompletedProcess(cmd, returncode=-1, stderr=str(e), stdout="")

    # ----------------------------------------------------------
    def _get_audio_duration(self, audio_path: str) -> float:
        """Probe audio file for its duration in seconds."""
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path]
        result = self._run_ffmpeg(probe_cmd, timeout=15)
        
        if result.returncode != 0:
            raise RuntimeError(f"Cannot read audio duration for '{audio_path}': {result.stderr}")
            
        try:
            info = json.loads(result.stdout)
            dur = float(info["format"]["duration"])
            if dur <= 0:
                raise ValueError(f"Audio duration is {dur}s — file may be corrupt: {audio_path}")
            return dur
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise RuntimeError(f"Error parsing audio duration for '{audio_path}': {e}")

    # ----------------------------------------------------------
    def merge_audio_video(self, overlays: list[str],
                          audio_path: str, output_name: str, recipe: dict = None, bgm_path: str = None) -> str:
        """
        Documentary format: concatenate clips → overlay audio → burn captions.
        """
        output_path = os.path.join(self.workspace_dir, f"{output_name}_merged.mp4")
        concat_path = os.path.join(self.workspace_dir, f"{output_name}_concat.mp4")
        sub_path = audio_path.replace(".mp3", ".ass")
        
        if not bgm_path:
            bgm_path = "" # MusicDirectorAgent always provides bgm_path

        audio_dur = self._get_audio_duration(audio_path)
        num_clips = max(len(overlays), 1)
        
        scenes_path = audio_path.replace(".mp3", "_scenes.json")
        scene_timings = None
        if os.path.exists(scenes_path):
            try:
                with open(scenes_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "scenes" in data and len(data["scenes"]) > 0:
                        scene_timings = data["scenes"]
            except Exception as e:
                logger.warning(f"Failed to read _scenes.json: {e}")

        # ── Channel DNA Setup ──
        slug = (self.channel_slug or "").lower()
        is_crime = "crime" in slug
        is_stoic = "stoic" in slug
        is_culture = "culture" in slug
        is_cricket = "cricket" in slug

        # If we have precise scene timings, match overlays to scenes
        if scene_timings:
            logger.info("Using Dynamic Scene System with LLM-Dictated Pacing.")
            
            clip_durations = []
            final_overlays = []
            overlay_offsets = []
            is_scene_boundary = []
            
            overlay_idx = 0
            for i, scene in enumerate(scene_timings):
                cdur = max(scene.get("end", 0) - scene.get("start", 0), 0.5)
                
                # Distribute all downloaded clips evenly among the remaining scenes
                if i == len(scene_timings) - 1:
                    clips_for_this_scene = max(1, len(overlays) - overlay_idx)
                else:
                    clips_for_this_scene = max(1, len(overlays) // max(1, len(scene_timings)))
                
                # Safeguard: prevent strobe-like editing by capping clips per scene
                max_clips_for_scene = max(1, int(cdur / 3.0))  # Minimum 3.0s per cut
                clips_for_this_scene = min(clips_for_this_scene, max_clips_for_scene)
                
                sub_cdur = cdur / clips_for_this_scene
                
                for _ in range(clips_for_this_scene):
                    base_clip = overlays[overlay_idx] if overlay_idx < len(overlays) else (overlays[overlay_idx % len(overlays)] if overlays else None)
                    if not base_clip:
                        break
                        
                    clip_durations.append(sub_cdur)
                    final_overlays.append(base_clip)
                    overlay_offsets.append(0.0)
                    is_scene_boundary.append(_ == 0)
                    overlay_idx += 1
            
            # Phase 2: J-Cuts / L-Cuts Engine
            # Stagger audio/video transitions to destroy the robotic "simultaneous cut" signature.
            for i in range(1, len(clip_durations)):
                if random.random() < 0.45:  # 45% chance to apply a J-Cut (Audio leads Video)
                    j_cut_offset = random.uniform(0.3, 0.6)
                    # Ensure current clip is long enough to be shortened
                    if clip_durations[i] > (j_cut_offset + 2.0):
                        clip_durations[i-1] += j_cut_offset
                        clip_durations[i] -= j_cut_offset
                        logger.info(f"Applied J-Cut at boundary {i}: Video delayed by {j_cut_offset:.2f}s")
                        
            overlays = final_overlays
            num_clips = len(overlays)
        else:
            logger.info("Fallback: Using mathematical clip slicing.")
            # Safeguard: prevent strobe-like editing. Minimum clip duration is 2.5s.
            if (audio_dur / num_clips) < 2.5:
                num_clips = max(1, int(audio_dur / 2.5))
                overlays = overlays[:num_clips]
                
            clip_dur = audio_dur / num_clips   # exact duration per clip
            clip_durations = [clip_dur] * num_clips
            overlay_offsets = [0.0] * num_clips
            is_scene_boundary = [True] * num_clips
            logger.info(f"Final cut speed: {clip_dur:.2f} seconds per clip (Total clips: {num_clips})")

        if not overlays:
            raise RuntimeError("[Assembler] No clips downloaded — cannot assemble video.")

        # Phase 3: Duration Sync & Padding
        # Whisper transcribed end-times often finish before the physical audio ends (due to trailing silence).
        # We pad the final clip to ensure the concatenated video stream outlives the audio stream, 
        # preventing freeze-frames before the final container is cut.
        total_video_dur = sum(clip_durations)
        if total_video_dur < audio_dur:
            clip_durations[-1] += (audio_dur - total_video_dur)
        clip_durations[-1] += 0.5  # Safety padding

        # Track all temp files for cleanup regardless of success/failure
        _temp_files = []
        # ── Step 1: Trim each clip to its exact share of the audio ───────────
        encoder_args = get_video_encoder_args()

        # Vary frame rate slightly per video (29-30fps) — identical fps across all videos is a bot signal
        fps_val = random.choice([29, 29, 30, 30, 30])  # weighted toward 30 but not always

        trimmed = []
        for i, clip_path in enumerate(overlays):
            trim_path = os.path.join(self.workspace_dir, f"{output_name}_trim{i}.mp4")
            current_clip_dur = clip_durations[i]
            offset = overlay_offsets[i] if scene_timings else 0.0
            
            # Subtle random adjustments to change frame hash without human-noticeable quality difference
            b_val = random.uniform(-0.015, 0.015)
            c_val = random.uniform(0.98, 1.02)
            s_val = random.uniform(0.97, 1.03)
            
            # Channel DNA Color Grading
            if is_stoic:
                b_val -= 0.1  # Darker
                c_val += 0.2  # Crushed blacks
                s_val = 0.2   # Almost monochrome
            elif is_crime:
                b_val -= 0.05
                c_val += 0.1
                s_val = 0.6   # Gritty, desaturated
            elif is_cricket or is_culture:
                b_val += 0.05
                c_val += 0.1
                s_val += 0.2  # Vibrant, punchy
            
            # Cinematic Engine: Confident static holds replacing forced zoompan
            vf_str = (
                f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,"
                f"eq=brightness={b_val:.4f}:contrast={c_val:.4f}:saturation={s_val:.4f},"
                f"fps=fps={fps_val},settb=1/{fps_val},setpts=PTS-STARTPTS"
            )
            
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{offset:.3f}",
                "-stream_loop", "-1",
                "-i", clip_path,
                "-t", f"{current_clip_dur:.3f}",
                "-vf", vf_str,
                "-r", str(fps_val),   # consistent fps across all clips
                "-an",
            ] + encoder_args + [trim_path]
            
            result = self._run_ffmpeg(cmd, timeout=120)
            if result.returncode == 0 and os.path.exists(trim_path):
                trimmed.append(trim_path)
                _temp_files.append(trim_path)
            else:
                logger.warning(f"Trim failed for clip {i}")

        if not trimmed:
            raise RuntimeError("[Assembler] All clip trims failed.")

        # ── Step 2: Concatenate trimmed clips ─────────────────────────────────
        concat_txt_path = os.path.join(self.workspace_dir, f"{output_name}_concat.txt")
        with open(concat_txt_path, "w", encoding="utf-8") as f:
            for t in trimmed:
                # ffmpeg concat file syntax requires paths to be properly escaped
                # Replacing single quotes for safety
                safe_t = os.path.abspath(t).replace("'", "'\\''")
                f.write(f"file '{safe_t}'\n")
        
        _temp_files.append(concat_txt_path)

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_txt_path,
            "-c:v", "copy",
            "-an",
            concat_path
        ]
        
        result = self._run_ffmpeg(cmd, timeout=180)
        if result.returncode != 0 or not os.path.exists(concat_path):
            # Cleanup trimmed clips before raising
            for _f in _temp_files:
                try:
                    os.remove(_f)
                except Exception:
                    pass
            raise RuntimeError("Concat failed")
            
        _temp_files.append(concat_path)
        logger.info(f"Clips joined (via Concat Filter): {concat_path}")

        # ── Step 3: Overlay audio (voice + BGM) + burn captions ──────────────
        ffmpeg_sub = sub_path.replace("\\", "/").replace(":", "\\:")
        
        recipe = recipe or {}
        bgm_vol = recipe.get("bgm_volume", 0.28)
        
        # Probe BGM duration and skip slow intro if track is long enough (e.g. > 30s)
        try:
            bgm_dur = self._get_audio_duration(bgm_path)
            trim_start = 15.0 if bgm_dur > 30.0 else 0.0
        except Exception as _bgm_err:
            logger.warning(f"BGM duration probe failed (using trim_start=0): {_bgm_err}")
            trim_start = 0.0
        
        # Dynamic 1.5-second background music fade-out at the exact end of the video
        fade_duration = 1.5
        fade_start = max(0.0, audio_dur - fade_duration)
        
        # Channel DNA Audio EQ
        voice_eq = "volume=1.3"
        if is_stoic:
            # Deep, resonant voice without the overlapping echo artifact
            voice_eq = "volume=1.5,bass=g=5:f=100"
        elif is_crime:
            voice_eq = "volume=1.4,treble=g=2:f=3000"
            
        # Build filter complex strings cleanly
        vf_ass = f"[0:v]ass='{ffmpeg_sub}'[vout]"
        af_voice = f"[1:a]{voice_eq}[voice]"
        af_bgm = (
            f"[2:a]atrim=start={trim_start},asetpts=PTS-STARTPTS,"
            f"loudnorm=I=-15:LRA=7:TP=-1.5,"
            f"volume={bgm_vol},afade=t=out:st={fade_start}:d={fade_duration}[bgm]"
        )
        af_mix = "[voice][bgm]amix=inputs=2:duration=first:normalize=0[aout]"
        
        fc = f"{vf_ass}; {af_voice}; {af_bgm}; {af_mix}"

        # Cut the video container exactly when the media content ends.
        effective_duration = audio_dur

        cmd = [
            "ffmpeg", "-y",
            "-i", concat_path,       # 0: video
            "-i", audio_path,        # 1: voice
            "-stream_loop", "-1",
            "-i", bgm_path,          # 2: bgm
            "-filter_complex", fc,
            "-map", "[vout]",
            "-map", "[aout]",
            "-t", f"{effective_duration:.3f}",
        ] + encoder_args + [
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            output_path
        ]

        result = self._run_ffmpeg(cmd, timeout=300)
        
        # Always cleanup temp files (success OR failure)
        for _f in _temp_files:
            try:
                os.remove(_f)
            except Exception:
                pass
            
        if result.returncode != 0:
            raise RuntimeError("Video merge failed")

        logger.info(f"Merged: {output_path}")
        return output_path

    # ----------------------------------------------------------
    def add_text_overlay(self, video_path: str, title: str, output_name: str) -> str:
        """
        Add channel name watermark at top of video.
        Falls back gracefully if font is missing.
        """
        output_path = os.path.join(self.workspace_dir, f"{output_name}_final.mp4")

        # Probe video duration for end-screen CTA timing
        try:
            _dur_res = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True
            )
            _vid_dur = float(_dur_res.stdout.strip())
        except Exception:
            _vid_dur = 55.0  # safe default

        # Cross-platform font resolution (Windows / Linux / macOS)
        raw_font = _resolve_font(_FONT_CANDIDATES_BOLD)
        font_path = raw_font.replace("\\", "/") if raw_font else None

        if font_path:
            # Escape colon in drive letter for ffmpeg filter syntax
            ffmpeg_font = font_path.replace(":", "\\:")
            safe_channel = self.channel_name.replace("'", "\\'").replace(":", "\\:")
            
            drawtext = (
                f"drawtext=fontfile='{ffmpeg_font}':"
                f"text='{safe_channel}':"
                f"fontsize=38:fontcolor=white@0.85:"
                f"x=(w-text_w)/2:y=55:"
                f"box=1:boxcolor=black@0.4:boxborderw=10"
            )
            # End-screen CTA: "FOLLOW FOR MORE" appears only in the last 2 seconds
            cta_start = max(0.0, _vid_dur - 2.0)
            cta_text = "FOLLOW FOR MORE"
            cta_drawtext = (
                f",drawtext=fontfile='{ffmpeg_font}':"
                f"text='{cta_text}':"
                f"fontsize=52:fontcolor=white@0.95:"
                f"x=(w-text_w)/2:y=(h-160):"
                f"box=1:boxcolor=black@0.55:boxborderw=14:"
                f"enable='gte(t,{cta_start:.2f})'"
            )
            drawtext = drawtext + cta_drawtext
        else:
            # No font found — skip text overlay entirely
            logger.warning("No font found — skipping text watermark.")
            shutil.copy(video_path, output_path)
            return output_path

        encoder_args = get_video_encoder_args()

        # Human-like MP4 metadata
        meta_args = [
            "-map_metadata", "-1",  # strip all auto-generated ffmpeg metadata
            "-metadata", f"comment={random.choice(_HUMAN_METADATA_COMMENTS)}",
            "-metadata", "encoder=", # blank encoder field
        ]

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", drawtext,
            "-codec:a", "copy",
        ] + encoder_args + meta_args + [
            output_path
        ]

        result = self._run_ffmpeg(cmd, timeout=180)
        if result.returncode != 0:
            logger.warning("Text overlay error (using video without watermark).")
            shutil.copy(video_path, output_path)
            return output_path

        logger.info(f"Final video: {output_path}")
        return output_path

    # ----------------------------------------------------------
    def generate_thumbnail(self, title: str, output_name: str, recipe: dict = None, video_path: str = None, thumb_text: str = None) -> str:
        """
        Generate a bold, high-contrast thumbnail.
        Uses thumb_text (3-4 word punch phrase from script) for the main text block.
        Falls back to title if thumb_text is not provided.
        """
        recipe = recipe or {}
        thumb_color = recipe.get("thumbnail_color", "Red")

        img = None
        if video_path and os.path.exists(video_path):
            temp_frame = os.path.join(self.workspace_dir, f"{output_name}_temp_frame.jpg")
            # Extract frame at 00:00:01.5 for culture channels to capture the hook, else 00:00:10
            ss_time = "00:00:01.5" if (self.channel_slug and "culture" in self.channel_slug.lower()) else "00:00:10"
            cmd = ["ffmpeg", "-y", "-ss", ss_time, "-i", video_path, "-vframes", "1", temp_frame]
            self._run_ffmpeg(cmd, timeout=60)
            
            if os.path.exists(temp_frame):
                try:
                    img = Image.open(temp_frame).convert("RGBA")
                    img = img.resize((1080, 1920))
                    os.remove(temp_frame)
                except Exception:
                    img = None

        if self.channel_slug and "culture" in self.channel_slug.lower():
            if img:
                thumb_path = os.path.join(self.thumbnail_dir, f"{output_name}_thumb.jpg")
                img.convert("RGB").save(thumb_path, "JPEG", quality=95)
                logger.info(f"Culture thumbnail (no text): {thumb_path}")
                return thumb_path

        if img is None:
            # Fallback: Solid Background
            img = Image.new("RGBA", (1080, 1920), color=(10, 10, 10))
            draw = ImageDraw.Draw(img)
            for y in range(1920):
                ratio = y / 1920
                v_high = int(220 * (1 - ratio) + 30 * ratio)
                v_low  = int(10 * (1 - ratio) + 5 * ratio)
                if thumb_color == "Blue": r, g, b = v_low, v_low, v_high
                elif thumb_color == "Green": r, g, b = v_low, v_high, v_low
                elif thumb_color == "Purple": r, g, b = int(150 * (1 - ratio) + 20 * ratio), v_low, v_high
                else: r, g, b = v_high, v_low, v_low
                draw.line([(0, y), (1080, y)], fill=(r, g, b, 255))
        else:
            # Video Frame Extracted: Apply Overlays
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            
            # Color Tint
            tint_map = {
                "Blue": (0, 0, 150, 70),
                "Green": (0, 150, 0, 70),
                "Purple": (100, 0, 150, 70),
                "Red": (150, 0, 0, 70)
            }
            draw.rectangle([(0, 0), (1080, 1920)], fill=tint_map.get(thumb_color, (150, 0, 0, 70)))
            
            # Bottom Dark Gradient
            for y in range(600, 1920):
                alpha = int(255 * ((y - 600) / 1320))
                draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
                
            img = Image.alpha_composite(img, overlay)

        draw = ImageDraw.Draw(img)

        # ── Channel-specific brand accent colors ─────────────────────────────
        # Read from dynamic JSON config rather than hardcoding names
        accent_list = self.ctx.config.get("visuals", {}).get("primary_text_color", [255, 220, 0]) if getattr(self, "ctx", None) and getattr(self.ctx, "config", None) else [255, 220, 0]
        accent = tuple(accent_list)

        # Wide accent bar spanning full width at ~55% height
        bar_y = 1050
        draw.rectangle([(0, bar_y), (1080, bar_y + 14)], fill=accent + (255,))

        # ── Font loading ──────────────────────────────────────────────────────
        try:
            bold_path    = _resolve_font(_FONT_CANDIDATES_BOLD)
            regular_path = _resolve_font(_FONT_CANDIDATES_REGULAR)
            font_big   = ImageFont.truetype(bold_path, 130) if bold_path else ImageFont.load_default()
            font_small = ImageFont.truetype(regular_path, 48) if regular_path else ImageFont.load_default()
        except Exception:
            font_big   = ImageFont.load_default()
            font_small = font_big

        # ── Main text: use thumbnail_text (3-4 words) over full title ─────────
        display_text = (thumb_text or title or "").upper()
        # Hard-cap at 22 chars per line to keep text huge
        lines = textwrap.wrap(display_text, width=12)
        if not lines:
            lines = [display_text[:12]]

        # Position text block below the accent bar
        y_start = bar_y + 40
        stroke_w = 6  # thick outline for readability over any background

        for line in lines[:3]:  # max 3 lines
            bbox = draw.textbbox((0, 0), line, font=font_big)
            w = bbox[2] - bbox[0]
            x = (1080 - w) // 2

            # Black stroke (drawn 8 directions for thick outline effect)
            for dx, dy in [(-stroke_w, 0), (stroke_w, 0), (0, -stroke_w), (0, stroke_w),
                           (-stroke_w, -stroke_w), (stroke_w, -stroke_w),
                           (-stroke_w, stroke_w), (stroke_w, stroke_w)]:
                draw.text((x + dx, y_start + dy), line, font=font_big, fill=(0, 0, 0, 255))

            # White fill on top
            draw.text((x, y_start), line, font=font_big, fill=(255, 255, 255, 255))
            y_start += 145

        # ── Channel name watermark at bottom ─────────────────────────────────
        ch_bbox = draw.textbbox((0, 0), self.channel_name.upper(), font=font_small)
        ch_w = ch_bbox[2] - ch_bbox[0]
        ch_x = (1080 - ch_w) // 2
        draw.text((ch_x + 2, 1862), self.channel_name.upper(), font=font_small, fill=(0, 0, 0, 180))
        draw.text((ch_x, 1860), self.channel_name.upper(), font=font_small, fill=accent + (220,))

        thumb_path = os.path.join(self.thumbnail_dir, f"{output_name}_thumb.jpg")
        img.convert("RGB").save(thumb_path, "JPEG", quality=95)
        logger.info(f"Thumbnail: {thumb_path}")
        return thumb_path

    # ----------------------------------------------------------
    def assemble(self, overlays: list[str], audio_path: str,
                 script: dict, output_name: str, recipe: dict = None, bgm_path: str = None) -> dict:
        """Full assembly pipeline."""
        merged = self.merge_audio_video(overlays, audio_path, output_name, recipe, bgm_path)
        final  = self.add_text_overlay(merged, script["title"], output_name)
        thumb  = self.generate_thumbnail(
            script["title"], output_name, recipe,
            video_path=merged,
            thumb_text=script.get("thumbnail_text"),  # 3-4 word punch phrase from script
        )

        return {
            "video":       final,
            "thumbnail":   thumb,
            "title":       script["title"],
            "description": script["description"],
            "tags":        script["tags"],
        }
