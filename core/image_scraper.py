"""
core/image_scraper.py
Fetches entity-specific images and videos via DuckDuckGo and converts them into Ken Burns MP4 clips.
This solves the fundamental retention killer of generic stock footage by pulling real news photos.
"""

import os
import hashlib
import uuid
import subprocess
import requests
from ddgs import DDGS
from config.settings import OUTPUT_DIR

class ImageScraper:
    def __init__(self, workspace_dir=None):
        self._workspace_dir = workspace_dir or OUTPUT_DIR
        os.makedirs(os.path.join(self._workspace_dir, "images"), exist_ok=True)

    def search_and_download_media(self, query: str, out_video_path: str, duration: float, is_crime: bool = False) -> bool:
        """
        Attempts to find a news video via DuckDuckGo. If that fails, falls back to a news image.
        Returns True if a video was successfully saved to out_video_path.
        """
        print(f"[ImageScraper] Searching DuckDuckGo News for: '{query}'")
        
        # 1. Try DuckDuckGo News Videos using yt-dlp
        try:
            import sys
            results = DDGS().videos(query, max_results=5)
            for res in results:
                url = res.get("content")
                if not url or "youtube.com" in url.lower() or "youtu.be" in url.lower():
                    # Skip youtube because video_clipper handles YouTube already
                    continue
                    
                print(f"[ImageScraper] Found news video URL: {url} -> Trying yt-dlp...")
                # We use a very strict 15s timeout because yt-dlp might hang on unsupported sites
                cmd = [
                    sys.executable, "-m", "yt_dlp",
                    "-f", "bestvideo[height>=480][ext=mp4]+bestaudio/best[height>=480]/best",
                    "--no-playlist",
                    "--max-downloads", "1",
                    "--timeout", "15",
                    "-o", out_video_path,
                    url
                ]
                try:
                    proc = subprocess.run(cmd, capture_output=True, timeout=25)
                    if proc.returncode == 0 and os.path.exists(out_video_path) and os.path.getsize(out_video_path) > 50000:
                        print(f"[ImageScraper] Successfully downloaded news video from {url}")
                        
                        tmp_raw = out_video_path + "_raw.mp4"
                        os.rename(out_video_path, tmp_raw)
                        
                        vf = (
                            "scale=1080:1920:force_original_aspect_ratio=increase,"
                            "crop=1080:1920,"
                            "setsar=1,"
                            "format=yuv420p"
                        )
                        if is_crime:
                            vf += ",vignette=PI/4,eq=contrast=1.1:brightness=-0.05"
                            
                        ffmpeg_cmd = [
                            "ffmpeg", "-y", "-i", tmp_raw, "-t", f"{duration:.3f}", 
                            "-vf", vf, "-c:v", "libx264", "-preset", "fast", 
                            "-pix_fmt", "yuv420p", "-r", "30", "-an", out_video_path
                        ]
                        subprocess.run(ffmpeg_cmd, capture_output=True, check=False)
                        try: os.remove(tmp_raw)
                        except: pass
                        
                        if os.path.exists(out_video_path):
                            return True
                except Exception as e:
                    pass
        except Exception as e:
            print(f"[ImageScraper] DDGS video search error: {e}")

        print(f"[ImageScraper] Searching DuckDuckGo Images for authentic evidence: '{query}'")
        try:
            results = DDGS().images(query, max_results=10)
            for res in results:
                image_url = res.get("image")
                if not image_url: continue
                
                try:
                    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                    r_img = requests.get(image_url, stream=True, headers=headers, timeout=10)
                    r_img.raise_for_status()
                    
                    tmp_img = os.path.join(self._workspace_dir, "images", f"tmp_{uuid.uuid4().hex}.jpg")
                    with open(tmp_img, "wb") as f:
                        for chunk in r_img.iter_content(chunk_size=8192):
                            f.write(chunk)
                            
                    if os.path.exists(tmp_img) and os.path.getsize(tmp_img) > 10000:
                        print(f"[ImageScraper] Successfully downloaded news image from {image_url}")
                        success = self.process_image_to_video(tmp_img, out_video_path, duration, is_crime)
                        try:
                            os.remove(tmp_img)
                        except Exception:
                            pass
                        
                        if success:
                            return True
                except Exception:
                    continue
        except Exception as e:
            print(f"[ImageScraper] DDGS image search error: {e}")
            
        return False

    def process_image_to_video(self, image_path: str, out_video_path: str, duration: float, is_crime: bool = False) -> bool:
        """
        Convert a static image into a 1080x1920 30fps MP4 using a Ken Burns zoompan.
        For landscape images, uses a vertical split-blur stack (blurred background with centered original image).
        For example_crime, adds a gritty dark overlay or keeps it raw.
        """
        print(f"[ImageScraper] Processing static image, outputting {duration}s video...")
        
        # Zoom-in only to avoid zoom-out issues
        h = int(hashlib.md5(image_path.encode()).hexdigest(), 16)
        pan_dir = h % 3
        
        z = "min(zoom+0.0005,1.5)"
        if pan_dir == 0:
            x = "(iw-iw/zoom)/2"
            y = "(ih-ih/zoom)/2"
        elif pan_dir == 1:
            x = "iw-iw/zoom"
            y = "ih-ih/zoom"
        else:
            x = "0"
            y = "0"

        # Check if the image is landscape
        is_landscape = False
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                width, height = img.size
                if width > height:
                    is_landscape = True
        except Exception as e:
            print(f"[ImageScraper] Failed to probe dimensions with PIL, defaulting to portrait: {e}")
            
        if is_landscape:
            print("[ImageScraper] Landscape image detected: applying vertical split-blur stack layout.")
            vf = (
                f"[0:v]split[bg][fg];"
                f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"zoompan=z='{z}':x='{x}':y='{y}':d={int(duration * 30)}:s=1080x1920:fps=30,boxblur=30[bg_blurred];"
                f"[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg_scaled];"
                f"[bg_blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2,"
                f"setsar=1,"
                f"format=yuv420p"
            )
            if is_crime:
                vf += ",vignette=PI/4,eq=contrast=1.1:brightness=-0.05"
            filter_args = ["-filter_complex", vf]
        else:
            vf = (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                "setsar=1,"
                "format=yuv420p,"
                f"zoompan=z='{z}':x='{x}':y='{y}':d={int(duration * 30)}:s=1080x1920:fps=30"
            )
            if is_crime:
                vf += ",vignette=PI/4,eq=contrast=1.1:brightness=-0.05"
            filter_args = ["-vf", vf]

        # Use ffmpeg directly (not via sys.executable -m which is invalid)
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", image_path,
            "-t", f"{duration:.3f}"
        ] + filter_args + [
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            out_video_path
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=120)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"[ImageScraper] FFmpeg failed or timed out: {e}")
            return False

    def get_image_clip(self, query: str, out_video_path: str, duration: float, is_crime: bool = False) -> str | None:
        """End-to-end method: Download news media, process it into MP4, return path."""
        if self.search_and_download_media(query, out_video_path, duration, is_crime):
            return out_video_path
        return None
