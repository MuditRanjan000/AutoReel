"""
execution/preview_captions.py
Quick-previews the caption style of the latest (or specified) video
by generating a test .ass file from a short sample sentence and 
running ffplay to show it over a black background.

Usage:
    python execution/preview_captions.py
    python execution/preview_captions.py --text "Your custom sample text here"

Requires: ffplay (part of ffmpeg), whisper, edge-tts
"""

import sys
import os
import subprocess
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.voiceover import VoiceoverGenerator
from config.settings import OUTPUT_DIR

SAMPLE_TEXT = (
    "These genius hackers just executed a multi-million dollar heist. "
    "Then did the one thing that landed them in federal prison forever. "
    "They forgot to click End Meeting."
)

def preview(text: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ass_path   = os.path.join(OUTPUT_DIR, "preview_voice.ass")

    print("[Preview] Generating voiceover + captions...")
    gen = VoiceoverGenerator()
    audio_path, _ = gen.generate(text, "preview_voice.mp3")

    print("[Preview] Launching ffplay preview (close window to exit)...")
    # Create a black video background and burn the subtitles
    ffmpeg_ass = ass_path.replace("\\", "/").replace(":", "\\:")
    cmd = [
        "ffplay", "-autoexit", "-f", "lavfi",
        "-i", f"color=c=black:s=1080x1920:r=30",
        "-vf", f"ass='{ffmpeg_ass}'",
        "-t", "60"
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("[Preview] ffplay not available. Open the .ass file manually:")
        print(f"  {ass_path}")
    else:
        print("[Preview] Done.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preview caption style")
    parser.add_argument("--text", default=SAMPLE_TEXT, help="Sample text to preview")
    args = parser.parse_args()
    preview(args.text)
