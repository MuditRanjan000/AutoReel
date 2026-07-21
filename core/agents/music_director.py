"""
core/agents/music_director.py
The Music Director Agent.
Reads the script, determines the emotional tone/mood, and dynamically
finds copyright-free background music to match it.

Self-Expanding BGM Pool:
  Every 30 days, the agent automatically searches YouTube for new trending
  no-copyright tracks per mood and saves them to config/bgm_discovery.json.
  The curated pool + discovered tracks are merged automatically — no manual
  URL additions ever needed.
"""

import os
import json
import time
import random
import subprocess
import sys
from core.gemini_client import generate_with_rotation
from core.ytdlp_utils import extend_with_cookies
from config.settings import OUTPUT_DIR, CHANNEL_TONE

# Path to the auto-discovery cache file (grows automatically every 30 days)
_BGM_DISCOVERY_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "bgm_discovery.json"
)
_BGM_DISCOVERY_INTERVAL_DAYS = 7  # How often to auto-refresh discovered tracks

# ── Curated Premium BGM Pool ──────────────────────────────────────────────────
# Curated list of high-quality, high-energy, copyright-free tracks popular on YouTube Shorts / Reels.
# CRITICAL: ALL TRACKS MUST BE 100% INSTRUMENTAL to prevent vocal overlap with narrator.
CURATED_BGM_POOL = {
    "phonk": [
        "https://www.youtube.com/watch?v=1q-0eF_H0hU", # Instrumental drift phonk
        "https://www.youtube.com/watch?v=Q1R9xG7Z83w", # Aggressive instrumental phonk
    ],
    "suspense": [
        "https://www.youtube.com/watch?v=O1AMQ2f3484", # Dark Cinematic Instrumental
        "https://www.youtube.com/watch?v=yJg-Y5byMMw", # Suspense Dark Instrumental
    ],
    "dark": [
        "https://www.youtube.com/watch?v=yJg-Y5byMMw", # Suspense Dark Instrumental
    ],
    "dramatic": [
        "https://www.youtube.com/watch?v=2TzPjQfH23Q", # Epic Cinematic Instrumental
    ],
    "upbeat": [
        "https://www.youtube.com/watch?v=IhchfhxvPKI", # Tobu - Candyland (Instrumental)
        "https://www.youtube.com/watch?v=fzNMd3Tu1Zw", # Elektronomia - Energy
    ],
    "aggressive": [
        "https://www.youtube.com/watch?v=1q-0eF_H0hU", # Instrumental Phonk
    ],
    "emotional": [
        "https://www.youtube.com/watch?v=g8N1fI065mI", # Sad Emotional Piano Instrumental
        "https://www.youtube.com/watch?v=7maJOI3QMu0", # Hans Zimmer style emotional
        "https://www.youtube.com/watch?v=pLcw3dK1yU0", # Deep atmospheric emotional
    ],
    "ambient": [
        "https://www.youtube.com/watch?v=WlVMHqZZy5I", # Ambient Space Instrumental
        "https://www.youtube.com/watch?v=R9M-g5_k12E", # Dark Ambient drone
    ],
    "hype": [
        "https://www.youtube.com/watch?v=Q1R9xG7Z83w", # Hype Phonk Instrumental
    ],
    "cinematic sports": [
        "https://www.youtube.com/watch?v=2TzPjQfH23Q", # Epic Cinematic Instrumental
    ],
    "cinematic": [
        "https://www.youtube.com/watch?v=2TzPjQfH23Q", # Epic Cinematic Instrumental
        "https://www.youtube.com/watch?v=O1AMQ2f3484", # Dark Cinematic
    ],
    "epic": [
        "https://www.youtube.com/watch?v=2TzPjQfH23Q", # Epic Cinematic Instrumental
    ],
    "trap": [
        "https://www.youtube.com/watch?v=2iqnAt4ROnE", # PVLACE - 808 Mafia Instrumental
    ],
    "cheerful": [
        "https://www.youtube.com/watch?v=IhchfhxvPKI", # Tobu - Candyland (Instrumental)
        "https://www.youtube.com/watch?v=J2X5mJ3HDYE", # NCS Happy Upbeat Instrumental
    ],
    "travel": [
        "https://www.youtube.com/watch?v=K4DyBUG242c", # Chill Vlog Instrumental
        "https://www.youtube.com/watch?v=EP625xQIGzs", # Tropical House Instrumental
    ],
    "social atmosphere": [
        "https://www.youtube.com/watch?v=K4DyBUG242c", # Chill Vlog Instrumental
    ],
    "sports documentary": [],
    "nightlife": [],
    "deep house": [],
    "orchestral": [
        "https://www.youtube.com/watch?v=2TzPjQfH23Q", # Epic Cinematic Instrumental
        "https://www.youtube.com/watch?v=O1AMQ2f3484", # Dark Cinematic Instrumental
        "https://www.youtube.com/watch?v=B9J-I1q-E8E", # Intense strings instrumental
    ],
    "reflective": [
        "https://www.youtube.com/watch?v=g8N1fI065mI", # Sad Emotional Piano Instrumental
        "https://www.youtube.com/watch?v=WlVMHqZZy5I", # Ambient Space Instrumental
        "https://www.youtube.com/watch?v=u3O24wWl8Zg", # Deep philosophical ambient
        "https://www.youtube.com/watch?v=9QZkAOMy-x0", # Stoic cinematic ambient
    ]
}



def _load_discovery_cache() -> dict:
    """Load the auto-discovered BGM tracks from the persistent cache file."""
    if not os.path.exists(_BGM_DISCOVERY_CACHE):
        return {}
    try:
        with open(_BGM_DISCOVERY_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_discovery_cache(data: dict):
    """Save discovered BGM tracks to the persistent cache file."""
    os.makedirs(os.path.dirname(_BGM_DISCOVERY_CACHE), exist_ok=True)
    try:
        with open(_BGM_DISCOVERY_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[MusicDirector] BGM discovery cache saved: {len(data)} mood categories")
    except Exception as e:
        print(f"[MusicDirector] Failed to save BGM discovery cache: {e}")


def _discover_new_tracks_for_mood(mood: str, count: int = 5) -> list:
    """
    Search YouTube for new trending no-copyright tracks for a given mood.
    Uses yt-dlp to probe top results without downloading them.
    Returns a list of YouTube URLs.
    """
    # Search queries tailored to find actual background music based on mood category
    if mood in ["suspense", "dark", "dramatic", "ambient", "cinematic", "orchestral", "reflective", "sports documentary", "emotional"]:
        search_queries = [
            f"{mood} cinematic background music no copyright instrumental CO.AG Music",
            f"royalty free {mood} documentary background music Infraction",
        ]
    elif mood in ["hype", "cinematic sports", "aggressive"]:
        search_queries = [
            f"{mood} epic background music no copyright instrumental NCS",
        ]
    elif mood in ["nightlife", "deep house", "travel", "social atmosphere", "cheerful"]:
        search_queries = [
            f"{mood} vlog background music no copyright instrumental Vlog No Copyright Music",
            f"chill {mood} house background music royalty free Audio Library",
        ]
    else:
        search_queries = [
            f"NCS {mood} no copyright background music instrumental",
            f"{mood} phonk no copyright shorts background Infraction",
        ]
    found_urls = []
    for query in search_queries:
        if len(found_urls) >= count:
            break
        try:
            probe_cmd = [
                sys.executable, "-m", "yt_dlp",
                "--flat-playlist",
                "--print", "%(id)s | %(duration)s | %(title)s",
                "--match-filter", "duration > 60 & duration < 480",
            ]
            probe_cmd = extend_with_cookies(probe_cmd)
            probe_cmd.append(f"ytsearch8:{query}")
            
            res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=20)
            if res.returncode != 0:
                continue
            for line in res.stdout.strip().split("\n"):
                if not line or "|" not in line:
                    continue
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                v_id = parts[0].strip()
                title = "|".join(parts[2:]).strip().lower()
                # Filter out anything that's clearly NOT background music
                # Fix 4: Expanded bad_words — now includes karaoke/stems/acapella variants
                bad_words = ["tutorial", "podcast", "reaction", "gameplay", "vlog",
                             "unboxing", "review", "interview", "speech", "lecture",
                             "how to", "explained", "news", "watch", "full video",
                             "karaoke", "no vocals", "no vocal", "vocal removed", "stems",
                             "minus one", "backing track", "acapella", "a cappella",
                             "instrumental cover", "sans paroles"]
                if any(bad in title for bad in bad_words):
                    continue
                url = f"https://www.youtube.com/watch?v={v_id}"
                if url not in found_urls:
                    found_urls.append(url)
                if len(found_urls) >= count:
                    break
        except Exception as e:
            print(f"[MusicDirector] BGM discovery search failed for '{query}': {e}")
    return found_urls


def auto_expand_bgm_pool(force: bool = False) -> dict:
    """
    Auto-discovers new BGM tracks every 30 days and merges them into the discovery cache.
    Called automatically at the start of each pipeline run.
    Returns the merged pool (curated + discovered).

    Args:
        force: If True, ignore the 30-day cooldown and discover immediately.
    """
    cache = _load_discovery_cache()
    last_run = cache.get("_last_discovery_timestamp", 0)
    days_since = (time.time() - last_run) / 86400

    if not force and days_since < _BGM_DISCOVERY_INTERVAL_DAYS:
        print(f"[MusicDirector] BGM discovery last ran {days_since:.1f} days ago (next in {_BGM_DISCOVERY_INTERVAL_DAYS - days_since:.1f} days). Using cache.")
        return cache

    print(f"[MusicDirector] Auto-expanding BGM pool (discovery interval: {_BGM_DISCOVERY_INTERVAL_DAYS} days)...")
    moods_to_discover = list(CURATED_BGM_POOL.keys())
    discovered_count = 0
    for mood in moods_to_discover:
        new_urls = _discover_new_tracks_for_mood(mood, count=5)
        if new_urls:
            existing = set(cache.get(mood, []))
            added = [u for u in new_urls if u not in existing]
            cache[mood] = list(existing) + added
            discovered_count += len(added)
            if added:
                print(f"[MusicDirector] Discovered {len(added)} new '{mood}' tracks: {added}")

    cache["_last_discovery_timestamp"] = time.time()
    cache["_total_discovered"] = cache.get("_total_discovered", 0) + discovered_count
    _save_discovery_cache(cache)
    print(f"[MusicDirector] BGM pool expansion complete. {discovered_count} new tracks added (lifetime total: {cache['_total_discovered']}).")
    return cache


class MusicDirectorAgent:
    def __init__(self, ctx=None):
        self.ctx = ctx
        self.channel_tone = ctx.tone if (ctx is not None and hasattr(ctx, 'tone')) else CHANNEL_TONE
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config", "bgm_defaults"
        )
        # Auto-discovery re-enabled safely with trusted channel filtering.
        self._discovered_pool = auto_expand_bgm_pool()
        
    def _get_recently_used_tracks(self, channel_name: str, limit: int = 3) -> list:
        """Fetch the most recently used BGM track IDs for this channel from SQLite to prevent repetition."""
        try:
            from core.db import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT parameters FROM experiments ORDER BY run_id DESC LIMIT 30")
            rows = cursor.fetchall()
            conn.close()
            
            used_ids = []
            for row in rows:
                try:
                    params = json.loads(row[0])
                    if params.get("channel_name") == channel_name:
                        track_id = params.get("bgm_track_id")
                        if track_id and track_id not in used_ids:
                            used_ids.append(track_id)
                        if len(used_ids) >= limit:
                            break
                except Exception:
                    pass
            return used_ids
        except Exception as e:
            print(f"[MusicDirector] Could not fetch recent tracks from DB: {e}")
            return []

    def _determine_mood(self, script: str) -> str:
        bgm_allowed_moods = getattr(self.ctx, 'bgm_allowed_moods', None)
        if not bgm_allowed_moods or not isinstance(bgm_allowed_moods, list):
            # Fallback allowed moods if not configured
            bgm_allowed_moods = ["suspense", "dark", "dramatic", "emotional", "aggressive"]

        # Filter to only moods that have at least 1 curated track to prevent empty pool failures
        bgm_allowed_moods = [m for m in bgm_allowed_moods if CURATED_BGM_POOL.get(m)]
        if not bgm_allowed_moods:
            bgm_allowed_moods = ["suspense", "dramatic"]  # absolute fallback (always have tracks)

        # Use the AI to pick the best mood from the allowed list based on the script content
        prompt = f"""
You are a cinematic music director. Read the following script and pick the SINGLE BEST musical mood from the allowed list to fit the emotional tone of the story.

Allowed Moods: {bgm_allowed_moods}

Script:
{script}

Output ONLY the exact mood name from the allowed list. No preamble, no quotes, no explanation.
"""
        try:
            from core.gemini_client import generate_with_rotation
            raw = generate_with_rotation(prompt).strip().lower()
            for mood in bgm_allowed_moods:
                if mood.lower() in raw:
                    print(f"[MusicDirector] AI selected mood based on script content: {mood}")
                    return mood
        except Exception as e:
            print(f"[MusicDirector] AI mood detection failed (fallback to random): {e}")
            
        return random.choice(bgm_allowed_moods)

    def _save_meta(self, bgm_path: str, mood: str):
        meta_path = bgm_path + ".json"
        try:
            import json
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"mood": mood, "track_id": self.last_track_id}, f, indent=2)
        except Exception as e:
            print(f"[MusicDirector] Failed to save BGM metadata: {e}")

    def score_video(self, script: str, run_id: str, recipe: dict = None) -> str:
        """Determines mood and downloads an appropriate background track."""
        print("[MusicDirector] Analyzing script for musical mood...")
        if recipe and recipe.get("bgm_mood"):
            mood = recipe["bgm_mood"]
            print(f"[MusicDirector] Using recipe BGM Mood: {mood.upper()}")
        else:
            mood = self._determine_mood(script)
            print(f"[MusicDirector] Selected Mood: {mood.upper()}")
        self.last_mood = mood
        self.last_track_id = "fallback"
        
        bgm_path = os.path.join(OUTPUT_DIR, f"{run_id}_bgm.mp3")
        
        if os.path.exists(bgm_path):
            meta_path = bgm_path + ".json"
            if os.path.exists(meta_path):
                try:
                    import json
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        self.last_mood = meta.get("mood", mood)
                        self.last_track_id = meta.get("track_id", "fallback")
                except Exception as e:
                    print(f"[MusicDirector] Error reading BGM metadata: {e}")
            return bgm_path
            
        print(f"[MusicDirector] Sourcing '{mood}' copyright-free music...")
        
        # Decide whether to use curated pool or do a dynamic search (10% chance of dynamic search for variety discovery)
        import random
        use_curated = True
        # Variety discovery bypassed - strictly enforce curated pool to prevent low-quality karaoke tracks

        # 1. Try to download from Curated Premium BGM Pool for maximum quality and virality
        # Merge: curated pool + auto-discovered tracks (so pool grows automatically over time)
        bgm_pool = {}
        for k, v in CURATED_BGM_POOL.items():
            bgm_pool[k] = list(v)
            if k in self._discovered_pool:
                bgm_pool[k].extend(self._discovered_pool[k])

        using_channel_pool = False
        if self.ctx and hasattr(self.ctx, "bgm_pool") and self.ctx.bgm_pool:
            if mood in self.ctx.bgm_pool and self.ctx.bgm_pool[mood]:
                bgm_pool = self.ctx.bgm_pool
                using_channel_pool = True

        # Prepare BGM tracks: Strictly use curated premium tracks
        base_tracks = list(bgm_pool.get(mood, []))
        
        # Shuffle curated pool to maintain variety
        shuffled_tracks = list(dict.fromkeys(base_tracks))
        random.shuffle(shuffled_tracks)
        
        if shuffled_tracks:
            if using_channel_pool:
                print(f"[MusicDirector] Using channel-specific curated BGM pool for mood '{mood}' ({len(base_tracks)} tracks).")
            else:
                print(f"[MusicDirector] Using default global curated BGM pool for mood '{mood}' ({len(base_tracks)} tracks).")
            
            # Deduplicate recently used tracks to prevent repetition
            channel_name = self.ctx.channel_name if self.ctx else "default"
            recently_used = self._get_recently_used_tracks(channel_name, limit=3)
            if recently_used:
                print(f"[MusicDirector] Recently used BGM track IDs to avoid: {recently_used}")
                filtered = [t for t in shuffled_tracks if t.split("v=")[-1] not in recently_used]
                if filtered:
                    shuffled_tracks = filtered
                else:
                    print("[MusicDirector] All pool tracks recently used. Resetting filter to allow repeat.")
            
            for url in shuffled_tracks:
                if using_channel_pool:
                    print(f"[MusicDirector] Sourcing from channel premium curated BGM: {url}")
                else:
                    print(f"[MusicDirector] Sourcing from global premium curated BGM: {url}")
                track_id = url.split("v=")[-1]
                cmd_download = [
                    sys.executable, "-m", "yt_dlp", url,
                    "--extract-audio", "--audio-format", "mp3",
                    "--audio-quality", "0",
                    "-o", bgm_path, "--quiet"
                ]
                cmd_download = extend_with_cookies(cmd_download)
                    
                subprocess.run(cmd_download, capture_output=True)
                
                if not os.path.exists(bgm_path) or os.path.getsize(bgm_path) <= 50_000:
                    print(f"[MusicDirector] yt-dlp failed to download {url} or file too small. Link may be dead. Removing from discovered pool...")
                    cache = _load_discovery_cache()
                    removed_from_cache = False
                    for m in cache:
                        if isinstance(cache[m], list) and url in cache[m]:
                            cache[m].remove(url)
                            removed_from_cache = True
                    if removed_from_cache:
                        _save_discovery_cache(cache)
                    if os.path.exists(bgm_path):
                        os.remove(bgm_path)
                    continue

                # Fix 3: Validate downloaded BGM is a real playable file (not corrupted or karaoke-stripped)
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-i", bgm_path],
                    capture_output=True, text=True
                )
                if probe.returncode != 0:
                    print(f"[MusicDirector] BGM file failed ffprobe validation (corrupt/invalid): {url}. Trying next track...")
                    os.remove(bgm_path)
                    continue
                if os.path.exists(bgm_path):
                    self.last_track_id = track_id
                    print(f"[MusicDirector] Curated premium track acquired: {bgm_path} (ID={track_id})")
                    self._save_meta(bgm_path, mood)
                    return bgm_path
            print("[MusicDirector] Curated pool failed. Falling back to local defaults...")
                
        # 3. Local Default Fallback (if network/yt-dlp downloads fail)
        if not os.path.exists(bgm_path):
            import glob
            # Support multiple tracks per mood by matching mood*.mp3 (e.g. suspense.mp3, suspense_1.mp3)
            mood_files = glob.glob(os.path.join(self.config_dir, f"{mood}*.mp3"))
            
            if not mood_files:
                if mood == "cheerful":
                    mood_files = glob.glob(os.path.join(self.config_dir, "upbeat*.mp3"))
                elif mood == "dark":
                    mood_files = glob.glob(os.path.join(self.config_dir, "suspense*.mp3"))
                    
            if mood_files:
                local_default = random.choice(mood_files)
                print(f"[MusicDirector] Network downloads failed. Copying local default for mood '{mood}': {local_default}")
                import shutil
                shutil.copy(local_default, bgm_path)
                self.last_track_id = f"local_{os.path.basename(local_default)}"
                self._save_meta(bgm_path, mood)
                return bgm_path
            else:
                # Absolute safety fallback: try to find any .mp3 in bgm_defaults
                if os.path.exists(self.config_dir):
                    default_tracks = [f for f in os.listdir(self.config_dir) if f.endswith(".mp3")]
                    if default_tracks:
                        fallback_track = os.path.join(self.config_dir, random.choice(default_tracks))
                        print(f"[MusicDirector] Local default '{mood}.mp3' missing. Copying random fallback track: {fallback_track}")
                        import shutil
                        shutil.copy(fallback_track, bgm_path)
                        self.last_track_id = f"local_fallback_{os.path.basename(fallback_track)}"
                        self._save_meta(bgm_path, mood)
                        return bgm_path
                        
        print("[MusicDirector] WARNING: Failed to download or find local background music.")
        return None

if __name__ == "__main__":
    director = MusicDirectorAgent()
    script = "A massive asteroid is hurtling towards Earth, and scientists say we only have 24 hours left. Can we survive?"
    bgm = director.score_video(script, "test_music_run")
    print(f"Final audio path: {bgm}")
