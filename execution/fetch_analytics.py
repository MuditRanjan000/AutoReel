"""
execution/fetch_analytics.py
Fetches YouTube Analytics data for all uploaded videos that are old enough
to have stable metrics (default: ≥48 hours after upload).

Updates experiments.json with real performance data so analyze_performance.py
can find what's working.

Usage:
    python execution/fetch_analytics.py
    python execution/fetch_analytics.py --force   # re-fetch already-fetched metrics
    python execution/fetch_analytics.py --dry-run # show what would be fetched, no writes

Requires:
  - YouTube OAuth token with yt-analytics.readonly scope
  - Run `python execution/authorize_youtube.py` if not yet authorized
"""

import sys
import os
import json
import argparse
import pickle
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from core.experiment_tracker import ExperimentTracker
from config.settings import (
    YOUTUBE_CLIENT_SECRETS_FILE, ANALYTICS_DELAY_HOURS, LOG_DIR
)

from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def get_credentials():
    """Load or refresh OAuth credentials with analytics scope for the active channel."""
    creds = None
    channel_name = os.environ.get("ACTIVE_CHANNEL", "example_channel_3")
    token_path = os.path.join("channels", f"{channel_name}_token.json")

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Save refreshed creds back to json
        token_data = {
            "token":         creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri":     creds.token_uri,
            "client_id":     creds.client_id,
            "client_secret": creds.client_secret,
            "scopes":        list(creds.scopes),
        }
        with open(token_path, "w") as f:
            json.dump(token_data, f, indent=2)
        return creds

    # Need fresh auth — user must run authorize_youtube.py
    print(f"[Analytics] No valid token found for {channel_name} at {token_path}.")
    print("[Analytics] Run: python execution/authorize_youtube.py")
    print("[Analytics] NOTE: The OAuth flow now requires yt-analytics.readonly scope.")
    return None


def fetch_video_analytics(analytics_client, video_id: str, upload_date: str) -> dict | None:
    """
    Fetch analytics for a single video using YouTube Analytics API v2.
    Returns a metrics dict or None on failure.
    """
    try:
        # Use upload date as start, today as end
        start_date = upload_date[:10]   # YYYY-MM-DD
        end_date   = datetime.now().strftime("%Y-%m-%d")

        response = analytics_client.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics=(
                "views,"
                "estimatedMinutesWatched,"
                "averageViewDuration,"
                "averageViewPercentage,"
                "likes,"
                "comments,"
                "shares,"
                "subscribersGained"
            ),
            filters=f"video=={video_id}",
        ).execute()

        rows = response.get("rows", [])
        if not rows:
            print(f"[Analytics] No data yet for video {video_id} (too new or no views)")
            return None

        headers = [h["name"] for h in response.get("columnHeaders", [])]
        values  = rows[0]
        raw     = dict(zip(headers, values))

        return {
            "views":                int(raw.get("views", 0)),
            "estimated_minutes_watched": float(raw.get("estimatedMinutesWatched", 0)),
            "avg_view_duration_s":  float(raw.get("averageViewDuration", 0)),
            "avg_view_percentage":  float(raw.get("averageViewPercentage", 0)),
            "likes":                int(raw.get("likes", 0)),
            "comments":             int(raw.get("comments", 0)),
            "shares":               int(raw.get("shares", 0)),
            "subscribers_gained":   int(raw.get("subscribersGained", 0)),
        }

    except HttpError as e:
        print(f"[Analytics] API error for {video_id}: {e}")
        return None
    except Exception as e:
        print(f"[Analytics] Unexpected error for {video_id}: {e}")
        return None


def run(force: bool = False, dry_run: bool = False):
    creds = get_credentials()
    if not creds:
        return False

    tracker = ExperimentTracker()
    stats   = tracker.summary()
    print(f"[Analytics] Database: {stats['total_runs']} runs | "
          f"{stats['uploaded']} uploaded | "
          f"{stats['with_metrics']} with metrics | "
          f"{stats['awaiting_analytics']} awaiting analytics")

    # Get experiments ready for analytics
    if force:
        # Re-fetch all uploaded experiments
        pending = [e for e in tracker.get_all() if e.get("video_id")]
    else:
        pending = tracker.get_experiments_needing_analytics(ANALYTICS_DELAY_HOURS)

    if not pending:
        print(f"[Analytics] Nothing to fetch. "
              f"(Videos need to be ≥{ANALYTICS_DELAY_HOURS}h old.)")
        return True

    print(f"[Analytics] Fetching metrics for {len(pending)} video(s)...")

    if dry_run:
        for exp in pending:
            print(f"  [DRY RUN] Would fetch: {exp['run_id']} | "
                  f"video={exp['video_id']} | "
                  f"uploaded={exp['uploaded_at'][:10]}")
        return True

    # Build analytics client
    analytics = build("youtubeAnalytics", "v2", credentials=creds)

    fetched = 0
    for exp in pending:
        video_id    = exp["video_id"]
        run_id      = exp["run_id"]
        upload_date = exp.get("uploaded_at", datetime.now().isoformat())

        print(f"[Analytics] Fetching: {run_id} (video: {video_id})")
        metrics = fetch_video_analytics(analytics, video_id, upload_date)

        if metrics:
            tracker.update_metrics(run_id, metrics)
            fetched += 1
            print(f"  views={metrics['views']} | "
                  f"retention={metrics['avg_view_percentage']:.1f}% | "
                  f"avg_watch={metrics['avg_view_duration_s']:.0f}s")
        else:
            print(f"  No data available yet — will retry next time.")

    print(f"\n[Analytics] Done. Fetched metrics for {fetched}/{len(pending)} videos.")
    print(f"[Analytics] Run `python execution/analyze_performance.py` to see findings.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch YouTube Analytics for all uploaded videos")
    parser.add_argument("--force",   action="store_true", help="Re-fetch already-fetched metrics")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()
    run(force=args.force, dry_run=args.dry_run)
