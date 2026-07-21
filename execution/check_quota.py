"""
execution/check_quota.py
Checks whether the configured Gemini API key has quota remaining
by making a minimal test request.

Usage:
    python execution/check_quota.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import google.generativeai as genai
from config.settings import GEMINI_API_KEY, GEMINI_MODEL

genai.configure(api_key=GEMINI_API_KEY)

def check_quota():
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content("Say: quota OK")
        print(f"[QuotaCheck] OK — Gemini is responding. Model: {GEMINI_MODEL}")
        print(f"[QuotaCheck] Response: {response.text.strip()}")
        return True
    except Exception as e:
        err = str(e)
        if "429" in err or "quota" in err.lower():
            print(f"[QuotaCheck] QUOTA EXHAUSTED — Free tier limit reached.")
            print(f"[QuotaCheck] See directives/manage_quota.md for solutions.")
            return False
        print(f"[QuotaCheck] ERROR: {e}")
        return False

if __name__ == "__main__":
    ok = check_quota()
    sys.exit(0 if ok else 1)
