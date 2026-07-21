from config.settings import CHANNELS_DIR, LOG_DIR, OUTPUT_DIR, THUMBNAIL_DIR, DB_PATH
"""
execution/run_experiments.py
Runs a local, non-upload experiment generating 5 distinct videos for each channel.
Forces distinct story selections using the RANDOM_STORY environment variable
and keeps all intermediate assets intact for visual inspection (--no-cleanup).
"""

import os
import sys
import subprocess
import time

def get_active_channels():
    channels_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), CHANNELS_DIR)
    if os.path.exists(channels_dir):
        return [f.replace(".json", "") for f in os.listdir(channels_dir) if f.endswith(".json")]
    return ["example_channel"]

CHANNELS = get_active_channels()
RUNS_PER_CHANNEL = 5

def log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [EXPERIMENT] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))

def main():
    log("=== STARTING MULTI-CHANNEL LOCAL EXPERIMENT RUN ===")
    log(f"Channels: {CHANNELS}")
    log(f"Videos per channel: {RUNS_PER_CHANNEL} (Total: {len(CHANNELS) * RUNS_PER_CHANNEL} videos)")
    log("Safety: AUTO_POST_YOUTUBE is disabled. All videos will stay local.")
    
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pipeline_script = os.path.join(root_dir, "execution", "run_pipeline.py")
    
    for channel in CHANNELS:
        log(f"\n==================================================")
        log(f"STARTING GENERATION FOR CHANNEL: {channel.upper()}")
        log(f"==================================================")
        
        for i in range(1, RUNS_PER_CHANNEL + 1):
            log(f"\n--- [{channel.upper()}] Run {i}/{RUNS_PER_CHANNEL} ---")
            
            # Setup environments
            env = os.environ.copy()
            env["ACTIVE_CHANNEL"] = channel
            env["RANDOM_STORY"] = "1"
            
            # Spawn pipeline run without uploading and keeping files
            cmd = [sys.executable, pipeline_script, "--no-cleanup"]
            
            try:
                result = subprocess.run(cmd, env=env, cwd=root_dir)
                if result.returncode == 0:
                    log(f"SUCCESS: [{channel.upper()}] Run {i} successfully completed!")
                else:
                    log(f"FAILED: [{channel.upper()}] Run {i} failed (exit code {result.returncode})")
            except Exception as e:
                log(f"CRASHED: [{channel.upper()}] Run {i} crashed: {e}")
                
            # Rest a tiny bit between runs to prevent API spikes
            time.sleep(2)
            
    log("\n==================================================")
    log("ALL EXPERIMENT RUNS COMPLETED!")
    log("Check output/videos/ and output/thumbnails/ for final assets.")
    log("==================================================")

if __name__ == "__main__":
    main()
