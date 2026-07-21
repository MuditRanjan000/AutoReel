import os
import sys
import glob
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from execution.run_pipeline import cleanup_run
from config.settings import OUTPUT_DIR, THUMBNAIL_DIR

def run_all_cleanup():
    print("=== AutoReel Channel Cleanup Start ===")
    
    # 1. Scan for run IDs from filenames in output/videos and output/thumbnails
    # Run ID pattern: YYYYMMDD_HHMMSS (15 characters: 8 digits, underscore, 6 digits)
    run_id_pattern = re.compile(r"^\d{8}_\d{6}")
    
    run_ids = set()
    for dir_path in [OUTPUT_DIR, THUMBNAIL_DIR]:
        if os.path.exists(dir_path):
            for fname in os.listdir(dir_path):
                match = run_id_pattern.match(fname)
                if match:
                    run_ids.add(match.group(0))
                    
    print(f"Found {len(run_ids)} unique run IDs to clean up: {sorted(list(run_ids))}")
    
    # 2. Run cleanup for each run ID, not keeping deliverables since user uploaded them
    total_cleaned = 0
    for run_id in sorted(list(run_ids)):
        print(f"Cleaning run ID: {run_id}...")
        deleted = cleanup_run(run_id, keep_deliverables=False)
        total_cleaned += len(deleted)
        
    # 3. Explicitly wipe the clips cache directory completely
    clips_cache_dir = os.path.join(OUTPUT_DIR, "clips_cache")
    cache_deleted = 0
    cache_bytes = 0
    if os.path.exists(clips_cache_dir):
        print(f"Cleaning clips cache directory: {clips_cache_dir}...")
        for fname in os.listdir(clips_cache_dir):
            fpath = os.path.join(clips_cache_dir, fname)
            if os.path.isfile(fpath):
                try:
                    size = os.path.getsize(fpath)
                    os.remove(fpath)
                    cache_deleted += 1
                    cache_bytes += size
                except Exception as e:
                    print(f"  Could not delete cache file {fpath}: {e}")
                    
    print(f"Wiped {cache_deleted} files from clips cache, freeing {cache_bytes / (1024*1024):.1f} MB.")
    print(f"Cleanup finished. Total run files deleted: {total_cleaned}")
    print("=== AutoReel Channel Cleanup Complete ===")

if __name__ == "__main__":
    run_all_cleanup()
