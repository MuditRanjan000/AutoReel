"""
execution/authorize_youtube.py
One-time OAuth2 authorization flow for YouTube Data API v3.
Run this script once to create token.json.

Prerequisites:
  1. Download OAuth credentials from Google Cloud Console
  2. Save as config/youtube_client_secrets.json
  3. Run: python execution/authorize_youtube.py

See directives/upload_to_youtube.md for full setup instructions.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
import json

SECRETS_FILE = "config/youtube_client_secrets.json"
SCOPES       = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

def authorize():
    if not os.path.exists(SECRETS_FILE):
        print(f"ERROR: {SECRETS_FILE} not found.")
        print("Download OAuth credentials from Google Cloud Console and save there.")
        print("See directives/upload_to_youtube.md for instructions.")
        return False

    print("=== AutoReel Multi-Channel Authorization ===")
    if len(sys.argv) > 1:
        channel_name = sys.argv[1].strip()
        print(f"Using channel name from CLI: {channel_name}")
    else:
        channel_name = input("Enter the channel profile name (e.g., example_channel_1, example_channel_2): ").strip()
    
    if not channel_name:
        print("ERROR: Channel name cannot be empty.")
        return False
        
    token_file = os.path.join("channels", f"{channel_name}_token.json")

    print(f"\nOpening browser to authorize: {channel_name.upper()}...")
    flow = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    # Save token
    token_data = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes),
    }
    with open(token_file, "w") as f:
        json.dump(token_data, f, indent=2)

    print(f"\n✅ Authorization complete! Token saved to {token_file}")
    print("You can now set AUTO_POST_YOUTUBE = True in config/settings.py")
    return True

if __name__ == "__main__":
    ok = authorize()
    sys.exit(0 if ok else 1)
