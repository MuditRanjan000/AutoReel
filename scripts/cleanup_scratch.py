import os
import glob

def cleanup_scratch_files():
    """
    Cleans up any AI-generated diagnostic files in the root directory.
    This includes files matching:
    - cloud_inspect*.py
    - test_yt*.py
    - fix_*.py
    - scratch_*.py
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"[Cleanup] Scanning {project_root} for leftover AI diagnostic files...")
    
    patterns = [
        "cloud_inspect*.py",
        "test_yt*.py",
        "fix_*.py",
        "scratch_*.py"
    ]
    
    deleted = 0
    for pattern in patterns:
        for match in glob.glob(os.path.join(project_root, pattern)):
            try:
                os.remove(match)
                print(f"[Cleanup] Deleted: {os.path.basename(match)}")
                deleted += 1
            except Exception as e:
                print(f"[Cleanup] Failed to delete {match}: {e}")
                
    print(f"[Cleanup] Finished. Deleted {deleted} temporary diagnostic files.")
    print("[Cleanup] NOTE: Future AI scratch files should be placed in .tmp/ or the IDE scratch directory.")

if __name__ == "__main__":
    cleanup_scratch_files()
