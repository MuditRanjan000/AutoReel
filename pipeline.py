"""
pipeline.py — COMPATIBILITY SHIM
=================================
This file is kept only so that any old bookmarks or notes still work.
The canonical pipeline has been moved to:

    execution/run_pipeline.py

Run directly:
    python execution/run_pipeline.py

Or via the CEO scheduler:
    python scheduler.py
"""

import subprocess
import sys
import os


def log(msg: str):
    """Kept for any legacy imports — delegates to a simple print."""
    from datetime import datetime
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def run_pipeline():
    """Legacy entry point — delegates to the canonical pipeline."""
    print("[pipeline.py] ⚠  This is a compatibility shim.")
    print("[pipeline.py] → Delegating to execution/run_pipeline.py ...")

    _root = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        [sys.executable, os.path.join(_root, "execution", "run_pipeline.py")],
        env=os.environ.copy(),
    )
    return result.returncode == 0


if __name__ == "__main__":
    _root = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        [sys.executable, os.path.join(_root, "execution", "run_pipeline.py")] + sys.argv[1:],
        env=os.environ.copy(),
    )
    sys.exit(result.returncode)
