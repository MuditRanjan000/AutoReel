from config.settings import CHANNELS_DIR, LOG_DIR, OUTPUT_DIR, THUMBNAIL_DIR, DB_PATH
import os
import sys
import subprocess
import time

def run_batch(channels, max_runs=5):
    print("==================================================")
    print(f"🚀 STARTING BATCH GENERATION")
    print(f"Channels: {', '.join(channels)}")
    print(f"Runs per channel: {max_runs}")
    print("==================================================\n")
    
    total_successful = 0
    total_failed = 0
    
    # Path to the actual pipeline
    pipeline_script = os.path.join(os.path.dirname(__file__), "run_pipeline.py")
    
    for channel in channels:
        print(f"\n📺 >>> SWITCHING TO ACTIVE CHANNEL: {channel} <<<")
        # Enforce that YouTube posting is disabled just in case someone changed settings.py
        env = os.environ.copy()
        env["ACTIVE_CHANNEL"] = channel
        env["AUTO_POST_YOUTUBE"] = "False"
        env["PYTHONUTF8"] = "1"
        
        for i in range(1, max_runs + 1):
            print(f"\n   [ {channel} | Run {i}/{max_runs} ]")
            print("   --------------------------------------")
            try:
                # Use subproccess to run the isolated pipeline cleanly per iteration
                result = subprocess.run([sys.executable, pipeline_script], env=env, encoding="utf-8")
                
                if result.returncode == 0:
                    print(f"   ✅ [ {channel} ] Run {i} SUCCESS.")
                    total_successful += 1
                else:
                    print(f"   ❌ [ {channel} ] Run {i} FAILED with code {result.returncode}.")
                    total_failed += 1
            except Exception as e:
                print(f"   ❌ [ {channel} ] Run {i} ENCOUNTERED FATAL ERROR: {e}")
                total_failed += 1
                
            # Brief cooldown between videos to be kind to the APIs and disk I/O
            if i < max_runs:
                time.sleep(2)
                
    print("\n==================================================")
    print(f"🎉 BATCH GENERATION COMPLETE!")
    print(f"✅ Successful Videos : {total_successful}")
    print(f"❌ Failed Videos     : {total_failed}")
    print(f"📁 Check 'output/videos/' and 'output/thumbnails/' for the results.")
    print("==================================================")

if __name__ == "__main__":
    import argparse
    import glob
    
    def get_all_channels():
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pattern = os.path.join(root, CHANNELS_DIR, "*.json")
        channels = []
        for f in glob.glob(pattern):
            name = os.path.splitext(os.path.basename(f))[0]
            if not name.endswith("_token") and name != "example_channel":
                channels.append(name)
        return channels

    parser = argparse.ArgumentParser(description="Batch generate videos without uploading.")
    parser.add_argument("--runs", type=int, default=5, help="Number of videos to generate per channel")
    parser.add_argument("--channels", nargs='+', default=get_all_channels(), help="List of channels to process")
    
    args = parser.parse_args()
    run_batch(args.channels, args.runs)
