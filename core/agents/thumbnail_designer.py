"""
core/agents/thumbnail_designer.py
The Thumbnail Designer Agent.

Thumbnail styles (randomized per run for format variation — avoids YouTube repetitive-content flags):
  Style A — News-broadcast left-panel: dark semi-transparent bar (left 75%), yellow accent stripe, white left-aligned text
  Style B — Centered headline: large centered text with bold stroke, gold top line + white main hook, no background bar
  Style C — Bottom split panel: full-width dark bar at bottom 30%, centered white text, red accent stripe on top
"""

import os
import subprocess
import time
import random
import urllib.parse
import requests
import textwrap
from PIL import Image, ImageDraw, ImageFont
from core.gemini_client import generate_with_rotation
from config.settings import THUMBNAIL_DIR, CHANNEL_NAME, POLLINATIONS_API_KEY
from core.channel_context import ChannelContext

# --- Cross-platform bold font candidates ---
_FONT_CANDIDATES_BOLD = [
    "C:/Windows/Fonts/ariblk.ttf",       # Arial Black — best for thumbnails
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/verdanab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]

def _resolve_font(candidates: list) -> str | None:
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


class ThumbnailDesigner:
    def __init__(self, ctx: ChannelContext = None):
        self.ctx = ctx
        self.thumbnail_dir = ctx.thumbnail_dir if (ctx is not None and hasattr(ctx, 'thumbnail_dir')) else THUMBNAIL_DIR
        self.channel_name = ctx.display_name if (ctx is not None and hasattr(ctx, 'display_name')) else CHANNEL_NAME
        self.channel_slug = ctx.channel_name if (ctx is not None and hasattr(ctx, 'channel_name')) else None
        os.makedirs(self.thumbnail_dir, exist_ok=True)

    def _create_prompt(self, title: str, script: str) -> str:
        prompt = f"""
        You are an expert YouTube Thumbnail Designer for '{self.channel_name}'.

        Video Title: {title}
        Script Snippet: {script[:300]}...

        Create a highly descriptive, comma-separated image generation prompt for a VIRAL YouTube Shorts thumbnail.
        It should be dramatic, cinematic, eye-catching, and highly detailed.
        Do NOT include any text in the image. No words, no letters, no signs with readable text.
        """
        if self.channel_slug == "example_culture":
            prompt += """
        CRITICAL DESIGN INSTRUCTION: The thumbnail image MUST feature an attractive adult woman matching the country being discussed, in an authentic, dramatic, or cultural setting related to the title. Avoid obvious stock-model aesthetics or generic studio poses; it must feel like a real travel or cultural moment. Ensure her face and expression are highly engaging. No text.
        """
        prompt += """
        Output ONLY the prompt, nothing else. Max 50 words.
        Example: A glowing robotic brain floating in a dark server room, neon blue lights, hyper-realistic, 8k resolution, cinematic lighting, dramatic shadows
        """
        return generate_with_rotation(prompt).strip()

    def _fetch_image(self, image_prompt: str, output_path: str, width: int = 576, height: int = 1024) -> bool:
        """
        Fetch image from Pollinations.ai with 3 retries + 5s backoff.
        Returns True if image was successfully downloaded.
        """
        encoded_prompt = urllib.parse.quote(image_prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&nologo=true&seed={int(time.time())}"
        
        headers = {}
        if POLLINATIONS_API_KEY:
            headers["Authorization"] = f"Bearer {POLLINATIONS_API_KEY.strip()}"
            url += f"&key={POLLINATIONS_API_KEY.strip()}"

        for attempt in range(1, 4):
            try:
                print(f"[ThumbnailDesigner] Pollinations attempt {attempt}/3...")
                response = requests.get(url, headers=headers, timeout=60)
                response.raise_for_status()
                # Validate it's actually an image (not an error HTML page)
                content_type = response.headers.get("Content-Type", "")
                if "image" not in content_type:
                    raise ValueError(f"Non-image response: {content_type}")
                with open(output_path, "wb") as f:
                    f.write(response.content)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 5000:
                    print(f"[ThumbnailDesigner] Raw AI image saved: {output_path}")
                    return True
                else:
                    print(f"[ThumbnailDesigner] Image file too small, retrying...")
            except Exception as e:
                print(f"[ThumbnailDesigner] Attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(5)

        print("[ThumbnailDesigner] All Pollinations attempts failed. Using PIL fallback.")
        return False

    def _create_fallback_image(self, output_path: str, thumb_color: str = "Blue") -> str:
        """Generate a cinematic gradient background using PIL if Pollinations is down."""
        img = Image.new("RGBA", (576, 1024), color=(10, 10, 10))
        draw = ImageDraw.Draw(img)
        color_map = {
            "Blue":   ((5, 10, 60), (0, 5, 30)),
            "Red":    ((60, 5, 10), (30, 0, 5)),
            "Green":  ((5, 60, 10), (0, 30, 5)),
            "Purple": ((40, 5, 60), (20, 0, 30)),
        }
        top_color, bot_color = color_map.get(thumb_color, color_map["Blue"])
        for y in range(1024):
            r = int(top_color[0] + (bot_color[0] - top_color[0]) * y / 1024)
            g = int(top_color[1] + (bot_color[1] - top_color[1]) * y / 1024)
            b = int(top_color[2] + (bot_color[2] - top_color[2]) * y / 1024)
            draw.line([(0, y), (576, y)], fill=(r, g, b, 255))
        img.convert("RGB").save(output_path, "JPEG", quality=95)
        return output_path

    def _stamp_news_bar_text(self, output_path: str, display_text: str):
        """
        Stamp the news-broadcast left-panel overlay onto the image:
          - Dark semi-transparent left bar (~60% width)
          - Yellow vertical accent stripe on far left (8px)
          - White bold left-aligned text inside bar, upper-middle zone
        """
        try:
            img = Image.open(output_path).convert("RGBA")
            width, height = img.size
            print(f"[ThumbnailDesigner] Stamping text onto dynamic dimensions: {width}x{height}")

            # ── 1. Dark semi-transparent overlay bar (left 75% of image for better mobile fit) ─────────
            bar_width = int(width * 0.75)
            bar_top   = int(height * 0.60)
            bar_bot   = int(height * 0.85)

            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ov_draw = ImageDraw.Draw(overlay)
            ov_draw.rectangle([(0, bar_top), (bar_width, bar_bot)], fill=(0, 0, 0, 185))
            img = Image.alpha_composite(img, overlay)

            draw = ImageDraw.Draw(img)

            # ── 2. Yellow vertical accent stripe (far left, full bar height) ──────
            draw.rectangle([(0, bar_top), (8, bar_bot)], fill=(255, 215, 0, 255))

            # ── 3. Bold white left-aligned text with DYNAMIC SCALING ──────────────
            max_font_size = int(width * 0.115)  # Ideal size
            x_left = 22  # left margin after the yellow stripe
            target_width = bar_width - x_left - 15  # right padding

            bold_path = _resolve_font(_FONT_CANDIDATES_BOLD)
            
            # Prepare lines
            lines = textwrap.wrap(display_text.upper().strip(), width=14, break_long_words=False)[:3]
            
            # Dynamic scaling loop
            current_font_size = max_font_size
            font_big = None
            
            while current_font_size > 30:
                font_big = ImageFont.truetype(bold_path, current_font_size) if bold_path else ImageFont.load_default()
                
                # Check if ANY line overflows
                overflow = False
                for line in lines:
                    if hasattr(draw, 'textbbox'):
                        left, top, right, bottom = draw.textbbox((0, 0), line, font=font_big)
                        text_w = right - left
                    else:
                        text_w, _ = draw.textsize(line, font=font_big)
                        
                    if text_w > target_width:
                        overflow = True
                        break
                
                if not overflow:
                    break
                current_font_size -= 5

            line_height = int(current_font_size * 1.22)
            total_h     = len(lines) * line_height
            y_text      = bar_top + (bar_bot - bar_top - total_h) // 2  # vertically center inside bar

            for line in lines:
                # Drop shadow
                draw.text(
                    (x_left + 3, y_text + 3),
                    line,
                    font=font_big,
                    fill=(0, 0, 0, 210),
                    stroke_width=4,
                    stroke_fill="black",
                )
                # Main text
                draw.text(
                    (x_left, y_text),
                    line,
                    font=font_big,
                    fill=(255, 255, 255, 255),
                    stroke_width=4,
                    stroke_fill="black",
                )
                y_text += line_height

            # ── 4. Save final JPEG ────────────────────────────────────────────────
            img.convert("RGB").save(output_path, "JPEG", quality=95)
            print(f"[ThumbnailDesigner] Successfully stamped scaled overlay text: '{display_text}'")

        except Exception as e:
            print(f"[ThumbnailDesigner] Non-fatal text stamp failure: {e}")

    def _stamp_centered_headline(self, output_path: str, display_text: str):
        """Style B: Large centered text, no background bar. Dramatic full-bleed look."""
        try:
            img = Image.open(output_path).convert("RGBA")
            width, height = img.size

            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ov_draw = ImageDraw.Draw(overlay)
            # Subtle full-image dark vignette to ensure text readability
            for y in range(height):
                alpha = int(80 * (abs(y - height / 2) / (height / 2)))
                ov_draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)

            bold_path = _resolve_font(_FONT_CANDIDATES_BOLD)
            lines = textwrap.wrap(display_text.upper().strip(), width=10, break_long_words=False)[:3]

            # Gold accent first word, white rest
            font_size = int(width * 0.13)
            font = ImageFont.truetype(bold_path, font_size) if bold_path else ImageFont.load_default()
            line_height = int(font_size * 1.25)
            total_h = len(lines) * line_height
            y_start = (height - total_h) // 2  # Vertically center

            for i, line in enumerate(lines):
                color = (255, 215, 0, 255) if i == 0 else (255, 255, 255, 255)  # Gold first, white rest
                # Get text width for centering
                if hasattr(draw, 'textbbox'):
                    l, t, r, b = draw.textbbox((0, 0), line, font=font)
                    text_w = r - l
                else:
                    text_w, _ = draw.textsize(line, font=font)
                x = (width - text_w) // 2
                # Shadow
                draw.text((x + 4, y_start + 4), line, font=font, fill=(0, 0, 0, 200), stroke_width=6, stroke_fill="black")
                # Main text
                draw.text((x, y_start), line, font=font, fill=color, stroke_width=5, stroke_fill="black")
                y_start += line_height

            img.convert("RGB").save(output_path, "JPEG", quality=95)
            print(f"[ThumbnailDesigner] Style B (Centered Headline) stamped: '{display_text}'")
        except Exception as e:
            print(f"[ThumbnailDesigner] Style B failed: {e}")

    def _stamp_bottom_panel(self, output_path: str, display_text: str):
        """Style C: Full-width dark bar at bottom 30%, centered text, red accent stripe on top edge."""
        try:
            img = Image.open(output_path).convert("RGBA")
            width, height = img.size

            bar_top = int(height * 0.72)
            bar_bot = height

            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ov_draw = ImageDraw.Draw(overlay)
            ov_draw.rectangle([(0, bar_top), (width, bar_bot)], fill=(0, 0, 0, 200))
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)

            # Red accent stripe on top edge of bar
            draw.rectangle([(0, bar_top), (width, bar_top + 8)], fill=(220, 30, 30, 255))

            bold_path = _resolve_font(_FONT_CANDIDATES_BOLD)
            lines = textwrap.wrap(display_text.upper().strip(), width=16, break_long_words=False)[:2]

            font_size = int(width * 0.10)
            font = ImageFont.truetype(bold_path, font_size) if bold_path else ImageFont.load_default()

            # Dynamic shrink if overflow
            while font_size > 28:
                font = ImageFont.truetype(bold_path, font_size) if bold_path else ImageFont.load_default()
                overflow = False
                for line in lines:
                    if hasattr(draw, 'textbbox'):
                        l, t, r, b = draw.textbbox((0, 0), line, font=font)
                        tw = r - l
                    else:
                        tw, _ = draw.textsize(line, font=font)
                    if tw > width - 40:
                        overflow = True
                        break
                if not overflow:
                    break
                font_size -= 4

            line_height = int(font_size * 1.2)
            total_h = len(lines) * line_height
            y_start = bar_top + 10 + (bar_bot - bar_top - 10 - total_h) // 2

            for line in lines:
                if hasattr(draw, 'textbbox'):
                    l, t, r, b = draw.textbbox((0, 0), line, font=font)
                    tw = r - l
                else:
                    tw, _ = draw.textsize(line, font=font)
                x = (width - tw) // 2
                draw.text((x + 3, y_start + 3), line, font=font, fill=(0, 0, 0, 200), stroke_width=4, stroke_fill="black")
                draw.text((x, y_start), line, font=font, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill="black")
                y_start += line_height

            img.convert("RGB").save(output_path, "JPEG", quality=95)
            print(f"[ThumbnailDesigner] Style C (Bottom Panel) stamped: '{display_text}'")
        except Exception as e:
            print(f"[ThumbnailDesigner] Style C failed: {e}")

    def _select_best_frame_with_gemini(self, candidate_paths: list) -> str | None:
        """
        Use Gemini's multimodal capabilities to evaluate candidate frames
        and select the one featuring a closeup/portrait of a beautiful local woman
        matching the channel's target demographic with no burned-in text.
        """
        from config.settings import GEMINI_API_KEYS, GEMINI_MODEL
        from google import genai
        from PIL import Image
        import json
        import re

        if not GEMINI_API_KEYS:
            print("[ThumbnailDesigner] No Gemini API keys configured. Skipping AI analyzer.")
            return None

        print(f"[ThumbnailDesigner] Evaluation started: Analyzing {len(candidate_paths)} candidate frames...")
        
        # Load and downscale candidate images to keep API payload small
        images_to_send = []
        path_map = {}
        for idx, path in enumerate(candidate_paths):
            try:
                img = Image.open(path).convert("RGB")
                img.thumbnail((540, 960))
                images_to_send.append(img)
                images_to_send.append(f"Candidate Frame {idx}")
                path_map[idx] = path
            except Exception as e:
                print(f"[ThumbnailDesigner] Failed to prepare candidate {path}: {e}")

        if not path_map:
            return None

        # Build prompt
        channel_desc = self.ctx.niche if (self.ctx and hasattr(self.ctx, 'niche')) else "cultural taboos, dating customs, and social norms around the world"
        
        is_cricket = self.channel_slug and "cricket" in self.channel_slug.lower()
        if is_cricket:
            criteria = """1. It MUST feature a clear closeup or medium shot of the main cricket player (e.g., Sachin Tendulkar) or an action-packed cricket moment (batsman hitting a shot, bowler in action, celebration).
2. It MUST NOT contain any burned-in graphics, channel logos, scoreboard overlays, or subtitles.
3. Avoid generic crowd shots, empty stadiums, or completely blurry action.
4. The subject must be sharp, clear, and center-focused."""
        else:
            criteria = """1. It MUST feature a closeup or medium shot of a beautiful young local woman as the primary focus, matching the target demographic. Her face, eyes, and expression must be clear, well-lit, and engaging.
2. It MUST NOT contain any burned-in graphics, subtitles, text blocks, or overlays.
3. Avoid generic scenery, street views, buildings, groups of people, or shots of men. The focus must be the woman.
4. Avoid blurry, low-quality, or poorly framed candidates."""

        prompt = f"""
You are an expert YouTube Thumbnail Selector.
Target Channel Niche: {channel_desc}
Your goal is to select the absolute best candidate frame to serve as a YouTube Shorts thumbnail.

The thumbnail MUST satisfy these criteria:
{criteria}

Analyze each of the candidate images provided. Decide which candidate matches the criteria best.
Return a JSON object containing the winning index and a brief reason.

Example Output:
{{
  "winning_index": 2,
  "reason": "Candidate 2 features a clear, well-lit portrait closeup of a beautiful East Asian woman with no text overlays."
}}
"""
        images_to_send.append(prompt)

        # Gemini Rotation call
        from core.gemini_client import _is_quota_error
        for i, api_key in enumerate(GEMINI_API_KEYS):
            label = f"key {i+1}/{len(GEMINI_API_KEYS)}"
            try:
                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=images_to_send
                )
                text = response.text.strip()
                print(f"[ThumbnailDesigner] Gemini analyzer response: {text}")
                
                # Parse output
                # Extract JSON block
                clean_text = text
                if "```" in clean_text:
                    blocks = re.findall(r"```(?:json)?\s*(.*?)\s*```", clean_text, re.DOTALL)
                    if blocks:
                        clean_text = blocks[0].strip()
                
                start_idx = clean_text.find('{')
                end_idx = clean_text.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    clean_text = clean_text[start_idx:end_idx+1]
                
                data = json.loads(clean_text)
                winner = int(data.get("winning_index", -1))
                if winner in path_map:
                    print(f"[ThumbnailDesigner] Gemini selected Candidate Frame {winner}: {path_map[winner]} (Reason: {data.get('reason')})")
                    return path_map[winner]
                else:
                    print(f"[ThumbnailDesigner] Gemini returned invalid index: {winner}")
            except Exception as e:
                if _is_quota_error(e):
                    print(f"[ThumbnailDesigner] Gemini key {i+1} exhausted — trying next...")
                    continue
                print(f"[ThumbnailDesigner] Non-fatal Gemini error: {e}")
                
        return None

    def _extract_best_frame(self, clip_paths: list, video_id: str) -> str | None:
        """
        Scan multiple clips at multiple timestamps. Return the path to the
        best extracted frame.
        Tries each clip at [1.5s, 3s, 5s, 8s].
        """
        candidates = []
        scan_timestamps = ["00:00:00.5", "00:00:01.5", "00:00:03.0", "00:00:04.5",
                           "00:00:06.0", "00:00:07.5", "00:00:09.0", "00:00:11.0"]

        for clip_idx, clip_path in enumerate(clip_paths[:4]):  # Check up to 4 clips
            if not clip_path or not os.path.exists(clip_path):
                continue
            for ts_idx, ts in enumerate(scan_timestamps):
                temp_path = os.path.join(
                    self.thumbnail_dir,
                    f"{video_id}_scan_c{clip_idx}_t{ts_idx}.jpg"
                )
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", ts,
                    "-i", clip_path,
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
                    "-vframes", "1",
                    temp_path
                ]
                res = subprocess.run(cmd, capture_output=True, timeout=15)
                if res.returncode == 0 and os.path.exists(temp_path):
                    size = os.path.getsize(temp_path)
                    if size > 5000:  # Must be at least 5KB — rules out black frames
                        candidates.append((size, temp_path))

        if not candidates:
            return None

        # Sort by size to have a fallback candidate list ordered by complexity
        candidates.sort(key=lambda x: x[0], reverse=True)
        candidate_paths = [c[1] for c in candidates]

        best_path = None
        # Use Gemini selector if this is a culture or cricket channel
        if self.channel_slug and ("culture" in self.channel_slug.lower() or "cricket" in self.channel_slug.lower()):
            print("[ThumbnailDesigner] Using Gemini-powered Multimodal frame selector...")
            best_path = self._select_best_frame_with_gemini(candidate_paths)

        # Fallback to OpenCV face detection if Gemini failed
        if not best_path:
            print("[ThumbnailDesigner] Gemini failed or unavailable. Falling back to OpenCV face detection...")
            try:
                import cv2
                face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
                for path in candidate_paths:
                    img = cv2.imread(path)
                    if img is not None:
                        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                        faces = face_cascade.detectMultiScale(gray, 1.1, 4)
                        if len(faces) > 0:
                            best_path = path
                            print(f"[ThumbnailDesigner] OpenCV selected frame with a face: {best_path}")
                            break
            except Exception as e:
                print(f"[ThumbnailDesigner] OpenCV fallback failed: {e}")

        # Final fallback to file-size heuristic if everything else failed
        if not best_path:
            best_path = candidates[0][1]
            print(f"[ThumbnailDesigner] Using fallback size-based frame: {best_path} ({candidates[0][0]:,} bytes)")

        # Clean up the rest of the candidate files
        for path in candidate_paths:
            if path != best_path:
                try:
                    os.remove(path)
                except Exception:
                    pass

        return best_path

    def generate(self, title: str, script: str, video_id: str,
                 thumbnail_text: str = None, thumb_color: str = "Blue",
                 first_clip_path: str = None, all_clip_paths: list = None) -> str:
        """Generates a thumbnail and returns the path.
        
        For Example_Channel_4: always extracts a real frame from the video clips.
        Scans multiple timestamps across multiple clips to find the best frame.
        Never uses Pollinations.ai for Example_Channel_4 — authenticity is the brand.
        """
        output_path = os.path.join(self.thumbnail_dir, f"{video_id}_thumbnail.jpg")

        # ── Example_Channel_4 / Example_Channel_2: Extract from real video clips ──────────
        is_culture = self.channel_slug and "culture" in self.channel_slug.lower()
        is_cricket = self.channel_slug and "cricket" in self.channel_slug.lower()
        
        if is_culture or is_cricket:
            if is_culture:
                print("[ThumbnailDesigner] Example_Channel_4: Scanning clips for best thumbnail frame...")
            else:
                print("[ThumbnailDesigner] Example_Channel_2: Scanning clips for best thumbnail frame...")
            
            # Build list of clips to scan: start with first_clip_path, then all others
            clips_to_scan = []
            if first_clip_path and os.path.exists(first_clip_path):
                clips_to_scan.append(first_clip_path)
            if all_clip_paths:
                for cp in all_clip_paths:
                    if cp and cp != first_clip_path and os.path.exists(cp):
                        clips_to_scan.append(cp)

            best_frame = None
            if clips_to_scan:
                # Prioritize scanning the first N clips based on channel type
                if is_culture:
                    best_frame = self._extract_best_frame(clips_to_scan[:2], video_id)
                else:  # is_cricket
                    best_frame = self._extract_best_frame(clips_to_scan[:3], video_id)
                
                # Fallback: scan remaining clips if the primary scan failed
                if not best_frame and len(clips_to_scan) > (2 if is_culture else 3):
                    print("[ThumbnailDesigner] Hook clip scan failed. Scanning fallback clips...")
                    best_frame = self._extract_best_frame(clips_to_scan[(2 if is_culture else 3):], video_id)

                if best_frame:
                    from core.utils import safe_atomic_replace
                    safe_atomic_replace(best_frame, output_path)
                    print(f"[ThumbnailDesigner] Success: Thumbnail extracted from video: {output_path}")
                else:
                    print("[ThumbnailDesigner] All frame extractions failed. Using PIL gradient fallback.")
            else:
                print("[ThumbnailDesigner] No clip paths available. Using PIL gradient fallback.")

            if not best_frame:
                # PIL gradient fallback — no Pollinations for Example_Channel_4/Example_Channel_2 fallback
                self._create_fallback_image(output_path, thumb_color)
            
            # For Example_Channel_4, return clean image directly (no text)
            if is_culture:
                return output_path, {"thumbnail_style": "Clean (No Text)"}
                
            # For Example_Channel_2, stamp text overlay on the extracted frame and return
            display_text = (thumbnail_text or title)
            style = random.choice(["A", "A", "B", "C"])
            print(f"[ThumbnailDesigner] Using layout style: {style}")
            if style == "A":
                self._stamp_news_bar_text(output_path, display_text)
            elif style == "B":
                self._stamp_centered_headline(output_path, display_text)
            else:
                self._stamp_bottom_panel(output_path, display_text)

            return output_path, {"thumbnail_style": style}

        # ── Non-Example_Channel_4: AI generation path ──────────────────────────────
        print("[ThumbnailDesigner] Brainstorming visual concept...")
        try:
            image_prompt = self._create_prompt(title, script)
        except Exception as e:
            print(f"[ThumbnailDesigner] Concept generation failed: {e}. Using generic prompt.")
            image_prompt = "dark cinematic dramatic news studio, neon lights, hyper-realistic, 8k resolution, cinematic lighting"
        print(f"[ThumbnailDesigner] Concept: {image_prompt}")

        print("[ThumbnailDesigner] Rendering image via Pollinations.ai...")
        ok = self._fetch_image(image_prompt, output_path)
        if not ok:
            print("[ThumbnailDesigner] Pollinations failed. Attempting clip-based frame extraction fallback...")
            best_frame = None
            clips_to_scan = []
            if first_clip_path and os.path.exists(first_clip_path):
                clips_to_scan.append(first_clip_path)
            if all_clip_paths:
                for cp in all_clip_paths:
                    if cp and cp != first_clip_path and os.path.exists(cp):
                        clips_to_scan.append(cp)
            
            if clips_to_scan:
                # Scan the first 3 clips for a good frame
                best_frame = self._extract_best_frame(clips_to_scan[:3], video_id)
                if best_frame:
                    from core.utils import safe_atomic_replace
                    safe_atomic_replace(best_frame, output_path)
                    print(f"[ThumbnailDesigner] Success: Fallback thumbnail extracted from video: {output_path}")
            
            if not best_frame:
                print("[ThumbnailDesigner] Frame extraction failed. Using PIL gradient fallback.")
                self._create_fallback_image(output_path, thumb_color)

        # Stamp text layout
        display_text = (thumbnail_text or title)
        style = random.choice(["A", "A", "B", "C"])
        print(f"[ThumbnailDesigner] Using layout style: {style}")
        if style == "A":
            self._stamp_news_bar_text(output_path, display_text)
        elif style == "B":
            self._stamp_centered_headline(output_path, display_text)
        else:
            self._stamp_bottom_panel(output_path, display_text)

        return output_path, {"thumbnail_style": style}


if __name__ == "__main__":
    designer = ThumbnailDesigner()
    designer.generate(
        title="OpenAI's New Secret Project Q-Star",
        script="OpenAI just leaked their most dangerous AI model yet.",
        video_id="test_pollinations",
        thumbnail_text="SECRET Q-STAR LEAKED",
        thumb_color="Blue",
    )
