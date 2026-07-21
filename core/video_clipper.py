"""
core/video_clipper.py
Downloads story-relevant footage using a two-source strategy:

  PRIMARY:  Pexels API  (free, 25k/month, properly tagged stock footage)
  FALLBACK: YouTube     (yt-dlp search, less reliable but always available)

Why Pexels?
  - Results are professionally shot and accurately tagged
  - Can filter by orientation=portrait (perfect for 9:16 Shorts)
  - Never returns "wrong" content for a query
  - Free API key from https://www.pexels.com/api/ (2 min, no credit card)

Clip strategy:
  - 6 clips × ~8s each = ~48s total with consistent cuts
  - Each clip tied to a specific moment in the script
  - No gaming backgrounds — full-screen documentary B-roll
"""

import subprocess
import os
import json
import random
import requests
import sys
from config.settings import OUTPUT_DIR, VIDEO_DURATION_SECONDS, PEXELS_API_KEYS, PEXELS_API_KEY, NICHE, PIXABAY_API_KEY, YOUTUBE_CHROMIUM_PROFILE_PATH
from core.gemini_client import generate_with_rotation
from core.image_scraper import ImageScraper
from core.retrieval_validator import validate_asset
from core.ytdlp_utils import extend_with_cookies

# ── Channel-Aware Fallback Queries ───────────────────────────────────────────
# These are LAST RESORT queries used only when ALL AI keys are exhausted.
# They are channel-specific so we always get at least the right type of footage.
FALLBACK_QUERIES_BY_NICHE = {
    # Culture Channel — culture/travel/dating niche: mixture of local women vlog hooks and scenery/food/sights
    "culture":  [
        {"source": "pexels", "query": "tokyo nightlife candid vlog", "pexels_query": "tokyo nightlife candid"},
        {"source": "youtube", "query": "medellin salsa nightlife", "pexels_query": "medellin salsa nightlife"},
        {"source": "pexels", "query": "ulaanbaatar cafe culture", "pexels_query": "ulaanbaatar cafe"},
        {"source": "youtube", "query": "rio beach volleyball social scene", "pexels_query": "rio beach volleyball"},
        {"source": "pexels", "query": "rome street cafe candid", "pexels_query": "rome street cafe"},
    ],
    # True Crime Channel — true crime, forensics, cctv, surveillance
    "crime": [
        {"source": "pexels", "query": "security camera cctv dark street", "pexels_query": "security camera cctv"},
        {"source": "pexels", "query": "police flashing lights night", "pexels_query": "police flashing lights"},
        {"source": "youtube", "query": "detective investigation crime scene tape", "pexels_query": "crime scene tape"},
        {"source": "pexels", "query": "forensic lab scientist microscope", "pexels_query": "forensic lab scientist"},
        {"source": "youtube", "query": "interrogation room detective footage", "pexels_query": "interrogation room detective"}
    ],
    # Cricket Channel — cricket matches, stadiums, players
    "cricket": [
        {"source": "pexels", "query": "cricket stadium stadium lights match", "pexels_query": "cricket stadium lights"},
        {"source": "pexels", "query": "cricket ball green grass field", "pexels_query": "cricket ball grass"},
        {"source": "pexels", "query": "cricket bats batsman player training", "pexels_query": "cricket bats batsman"},
        {"source": "pexels", "query": "cheering stadium crowd fans match", "pexels_query": "stadium crowd fans"}
    ],
    # Stoic Channel — statues, Roman/Greek ruins, rain, meditation
    "stoic": [
        {"source": "pexels", "query": "ancient greek roman statue sculpture monument", "pexels_query": "ancient roman statue"},
        {"source": "pexels", "query": "dark moody rain street silhouette window", "pexels_query": "dark moody rain"},
        {"source": "pexels", "query": "man standing looking night starry sky forest", "pexels_query": "starry sky night"},
        {"source": "pexels", "query": "roman pillars architecture ancient ruins", "pexels_query": "roman pillars ruins"},
        {"source": "pexels", "query": "meditation silhouette sunset ocean waves", "pexels_query": "meditation silhouette sunset"}
    ],
    # Tech Channel
    "tech": [
        {"source": "pexels", "query": "server room data center mainframe tech", "pexels_query": "server room data center"},
        {"source": "pexels", "query": "cyberpunk digital interface hologram HUD", "pexels_query": "digital interface hologram"},
        {"source": "pexels", "query": "developer typing code computer monitor glow", "pexels_query": "developer typing code"},
        {"source": "pexels", "query": "robot artificial intelligence electronic circuit board", "pexels_query": "robot circuit board"}
    ],
    # Generic default
    "default": [
        {"source": "pexels", "query": "beautiful landscape travel destination nature", "pexels_query": "beautiful landscape travel"},
        {"source": "pexels", "query": "office worker writing notebook coffee cup", "pexels_query": "office worker writing"},
        {"source": "pexels", "query": "cinematic abstract lights blurry background bokeh", "pexels_query": "abstract lights bokeh"}
    ],
}

def _get_fallback_queries(niche: str) -> list[dict]:
    """Return channel-matched fallback queries based on niche keywords."""
    n = niche.lower()
    if any(k in n for k in ["culture", "dating", "travel", "taboo", "social norm"]):
        return FALLBACK_QUERIES_BY_NICHE["culture"]
    if any(k in n for k in ["crime", "murder", "heist", "forensic", "unsolved", "police"]):
        return FALLBACK_QUERIES_BY_NICHE["crime"]
    if any(k in n for k in ["cricket", "sports", "player", "match"]):
        return FALLBACK_QUERIES_BY_NICHE["cricket"]
    if any(k in n for k in ["stoic", "philosophy", "discipline", "mindset", "ancient"]):
        return FALLBACK_QUERIES_BY_NICHE["stoic"]
    if any(k in n for k in ["tech", "ai", "artificial intelligence", "crypto", "finance", "wealth", "money"]):
        return FALLBACK_QUERIES_BY_NICHE["tech"]
    return FALLBACK_QUERIES_BY_NICHE["default"]


class VideoClipper:

    def __init__(self, ctx=None):
        self._ctx = ctx
        self._workspace_dir = ctx.workspace_dir if (ctx is not None and hasattr(ctx, 'workspace_dir')) else OUTPUT_DIR
        self._video_duration = ctx.video_duration_seconds if (ctx is not None and hasattr(ctx, 'video_duration_seconds')) else VIDEO_DURATION_SECONDS
        self._niche = ctx.niche if (ctx is not None and hasattr(ctx, 'niche')) else NICHE
        os.makedirs(self._workspace_dir, exist_ok=True)

    # ── Source Routing ────────────────────────────────────────────────────────

    @staticmethod
    def _is_person_query(query: str) -> bool:
        """
        Universal person/proper-noun detector — works for ALL channels.

        Returns True if the query appears to reference a specific person,
        team, company, or news event that Pexels will NEVER have footage of.

        Strategy: look for 2+ consecutive Title-Case words (i.e. a proper noun
        phrase). This catches:
          - Vaibhav Suryavanshi, Sam Altman, Elon Musk, Virat Kohli
          - OpenAI, Apple Vision Pro, Mumbai Indians, Manchester City
          - Any proper name across cricket, tech, wealth, culture channels

        Skips very common Title-Case sentence starters ("The", "A", "In", etc.)
        to avoid false positives on generic descriptions.
        """
        import re
        # Common single-capitalised words that aren't proper nouns in queries
        NOT_PROPER = {
            "The", "A", "An", "In", "On", "At", "For", "By", "With", "And",
            "Or", "But", "Of", "Is", "Are", "Was", "Has", "Have", "To",
            "From", "Into", "Up", "Down", "Out", "Off", "Over",
            "Man", "Woman", "People", "City", "Street", "World", "Life",
            "Day", "Night", "Time", "New", "Big", "Live",
        }
        tokens = query.split()
        run = 0
        for token in tokens:
            # Strip punctuation for matching
            word = re.sub(r"[^A-Za-z']", "", token)
            if word and word[0].isupper() and word not in NOT_PROPER:
                run += 1
                if run >= 2:
                    return True   # 2+ consecutive proper-noun tokens → person/entity
            else:
                run = 0
        return False

    # ── Query Generation ──────────────────────────────────────────────────────

    def _get_clip_queries(self, script_data: dict, recipe: dict = None) -> list[dict]:
        """
        Extract clip queries directly from the script_data scenes (Dynamic Scene System).
        No redundant LLM calls here.
        """
        scenes = script_data.get("scenes", [])
        if not scenes:
            print("[Clipper] No scenes found in script_data. Using fallbacks.")
            return _get_fallback_queries(self._niche)

        queries = []
        for scene in scenes:
            visuals = scene.get("visuals", [])
            # Backward compatibility
            if not visuals and (scene.get("query") or scene.get("tier1_query")):
                visuals = [{"type": "primary", "query": scene.get("query") or scene.get("tier1_query"), "intent": scene.get("visual_intent", "")}]
                
            for v in visuals:
                query = v.get("query") or v.get("tier1_query") or ""
                if not query:
                    continue
                    
                # Read banned sources from dynamic JSON config
                banned_sources = self._ctx.raw_config.get("visuals", {}).get("banned_sources", [])
                
                source = "pexels"
                if "pexels" in banned_sources:
                    if any(w in query.lower() for w in ["mugshot", "evidence", "photo", "news article", "article", "newspaper"]):
                        source = "image"
                    else:
                        source = "youtube"
                elif self._is_person_query(query) and any(w in self._ctx.raw_config.get("NICHE", "").lower() for w in ["sports", "cricket"]):
                    source = "youtube"
                elif "cinematic" in self._ctx.raw_config.get("bgm_allowed_moods", []):
                    source = "pexels"
                elif self._is_person_query(query) or any(w in query.lower() for w in ["nightlife", "dating", "relationship", "street interview", "interview", "social scene", "festival", "street food", "local culture", "travel vlog", "vlog", "club", "bar", "night market", "party", "carnival", "social interaction", "expat", "tourist", "couple", "tiktok", "amateur", "documentary", "news"]):
                    source = "youtube"
                    
                queries.append({
                    "source": source,
                    "query": query,
                    "pexels_query": query,
                    "tier1_query": v.get("tier1_query", query),
                    "tier2_query": v.get("tier2_query", query),
                    "tier3_query": v.get("tier3_query", query),
                    "tier4_query": v.get("tier4_query", query),
                    "visual_intent": v.get("intent", ""),
                    "visual_type": v.get("type", "primary"),
                    "narration": scene.get("narration", "")
                })
            
        print(f"[Clipper] Extracted {len(queries)} queries directly from Dynamic Scene System")
        return queries

    # ── Pexels Source ─────────────────────────────────────────────────────────

    def _map_to_pexels_demographic(self, query: str) -> str:
        """
        Map specific nationalities or country names to broader regional/demographic
        terms that Pexels/Pixabay APIs understand, preventing 0-result stock failures.
        """
        mapping = {
            r'\bsri\s*lankan?\b': 'south asian',
            r'\bnepali\b': 'south asian',
            r'\bbangladeshi\b': 'south asian',
            r'\bpakistani\b': 'south asian',
            r'\bvietnamese\b': 'southeast asian',
            r'\bthai\b': 'southeast asian',
            r'\bfilipina\b': 'southeast asian',
            r'\bfilipino\b': 'southeast asian',
            r'\bindonesian\b': 'southeast asian',
            r'\bmalaysian\b': 'southeast asian',
            r'\bcambodian\b': 'southeast asian',
            r'\blaotian\b': 'southeast asian',
            r'\bjapanese\b': 'east asian',
            r'\bkorean\b': 'east asian',
            r'\bchinese\b': 'east asian',
            r'\btaiwanese\b': 'east asian',
            r'\bethiopian\b': 'east african',
            r'\bkenyan\b': 'east african',
            r'\bnigerian\b': 'west african',
            r'\bghanaian\b': 'west african',
            r'\bsomali\b': 'east african',
            r'\bsardinian\b': 'mediterranean',
            r'\bcolombian\b': 'latina',
            r'\bbrazilian\b': 'latina',
            r'\bmexican\b': 'latina',
            r'\bvenezuelan\b': 'latina',
            r'\bperuvian\b': 'latina',
        }
        
        import re
        mapped_query = query
        for pattern, replacement in mapping.items():
            mapped_query = re.sub(pattern, replacement, mapped_query, flags=re.IGNORECASE)
        
        if mapped_query != query:
            print(f"[Clipper] Mapped stock query: '{query}' -> '{mapped_query}'")
        return mapped_query

    def _search_pexels(self, query: str, out_path: str, duration: float, exclude_ids: set = None) -> str | None:
        """
        Search Pexels for portrait stock footage, rotating through all API keys
        on rate-limit (429) or error — same pattern as Gemini key rotation.

        [Phase 1] Relevance fix: Pexels results are already ranked by their
        search engine. We no longer shuffle them (which destroyed that ranking).
        Instead we score each video by keyword overlap between query words and
        the video URL slug, then iterate in scored order, skipping used IDs.
        """
        if not PEXELS_API_KEYS:
            return None

        # Map query to generic demographic terms to prevent 0-result stock failures
        query = self._map_to_pexels_demographic(query)

        # Pre-compute query keywords for scoring (strip stop words)
        _STOP = {"a", "an", "the", "in", "on", "at", "for", "of", "and", "or", "is", "to", "with", "by"}
        query_words = set(query.lower().split()) - _STOP

        for idx, key in enumerate(PEXELS_API_KEYS):
            try:
                headers = {"Authorization": key}
                params  = {
                    "query":       query,
                    "orientation": "portrait",
                    "per_page":    30,
                }
                resp = requests.get(
                    "https://api.pexels.com/videos/search",
                    headers=headers, params=params, timeout=15
                )

                if resp.status_code == 429:
                    print(f"[Clipper] Pexels key {idx+1}/{len(PEXELS_API_KEYS)} rate-limited, trying next...")
                    continue
                if resp.status_code != 200:
                    print(f"[Clipper] Pexels key {idx+1} HTTP {resp.status_code}, trying next...")
                    continue

                videos = resp.json().get("videos", [])
                if not videos:
                    print(f"[Clipper] Pexels: no results for '{query}'")
                    return None   # No results = query issue, not key issue

                # [Phase 1A] Score by URL-slug keyword overlap instead of random shuffle.
                # Pexels URL slugs (e.g. "pexels-cottonbro-5532772-police-siren-night")
                # often contain descriptive keywords we can match against the query.
                def _pexels_relevance(video):
                    url = video.get("url", "").lower()
                    slug_words = set(url.replace("-", " ").replace("_", " ").split())
                    overlap = len(query_words & slug_words)
                    # Secondary signal: Pexels API rank (index in response = relevance rank)
                    # We preserve this as a tiebreaker by NOT shuffling
                    return overlap

                # Sort by relevance score descending; original API order is tiebreaker
                scored = sorted(enumerate(videos), key=lambda iv: _pexels_relevance(iv[1]), reverse=True)
                sorted_videos = [v for _, v in scored]

                for video in sorted_videos:
                    vid_id = str(video.get("id", ""))
                    if exclude_ids is not None and vid_id in exclude_ids:
                        continue
                    files = video.get("video_files", [])
                    # Prefer native portrait files that are HD, but keep landscape HD as fallback
                    portrait_files = [f for f in files if f.get("width", 1) < f.get("height", 0)]
                    portrait_hd = [f for f in portrait_files if f.get("height", 0) >= 720]
                    landscape_hd = [f for f in files if f.get("width", 1) >= f.get("height", 0) and f.get("height", 0) >= 720]
                    
                    hd_candidates = portrait_hd or landscape_hd
                    if not hd_candidates:
                        print(f"[Clipper] Pexels: video {video.get('id')} has no HD files (height >= 720), checking next candidate video...")
                        continue
                        
                    best = max(hd_candidates, key=lambda f: f.get("height", 0), default=None)
                    if not best or not best.get("link"):
                        continue

                    rel_score = _pexels_relevance(video)
                    print(f"[Clipper] Pexels key {idx+1}: downloading high-res clip ({best.get('width')}x{best.get('height')}) | relevance_score={rel_score}...")
                    r = requests.get(best["link"], stream=True, timeout=120)
                    r.raise_for_status()
                    with open(out_path, "wb") as fp:
                        for chunk in r.iter_content(chunk_size=65536):
                            fp.write(chunk)

                    if os.path.exists(out_path) and os.path.getsize(out_path) > 10_000:
                        if exclude_ids is not None and vid_id:
                            exclude_ids.add(vid_id)
                        return out_path

                print(f"[Clipper] Pexels: no usable file in results for '{query}'")
                return None

            except Exception as e:
                print(f"[Clipper] Pexels key {idx+1} error: {e}")
                continue

        print(f"[Clipper] All Pexels keys failed for '{query}'")
        return None

    def _search_pixabay(self, query: str, out_path: str, duration: float, exclude_ids: set = None) -> str | None:
        """
        Search Pixabay Video API for stock footage.

        [Phase 1C] Relevance fix: Pixabay returns a free `tags` field per hit
        (comma-separated). We score hits by tag-overlap with the query before
        selecting, rather than random.shuffle which discards that signal.
        """
        if not PIXABAY_API_KEY:
            return None
            
        # Map query to generic demographic terms to prevent 0-result stock failures
        query = self._map_to_pexels_demographic(query)

        # Pre-compute query keywords for tag matching
        _STOP = {"a", "an", "the", "in", "on", "at", "for", "of", "and", "or", "is", "to", "with", "by"}
        query_words = set(query.lower().split()) - _STOP

        try:
            params = {
                "key": PIXABAY_API_KEY,
                "q": query,
                "per_page": 30,
                "safesearch": "true"
            }
            resp = requests.get("https://pixabay.com/api/videos/", params=params, timeout=15)
            if resp.status_code != 200:
                print(f"[Clipper] Pixabay HTTP {resp.status_code}: {resp.text[:100]}")
                return None
                
            hits = resp.json().get("hits", [])
            if not hits:
                print(f"[Clipper] Pixabay: no results for '{query}'")
                return None

            # [Phase 1C] Score by tag overlap — Pixabay provides free comma-separated tags per hit
            def _pixabay_relevance(hit):
                tag_str = hit.get("tags", "").lower()
                tag_words = set(tag_str.replace(",", " ").split())
                return len(query_words & tag_words)

            hits.sort(key=_pixabay_relevance, reverse=True)
                
            # Grab the best video (preferring large/medium MP4s to ensure height >= 720)
            for hit in hits:
                vid_id = str(hit.get("id", ""))
                if exclude_ids is not None and vid_id in exclude_ids:
                    continue
                videos = hit.get("videos", {})
                best = None
                for size_key in ["large", "medium"]:
                    cand = videos.get(size_key)
                    if cand and cand.get("url") and cand.get("height", 0) >= 720:
                        best = cand
                        break
                if not best:
                    print(f"[Clipper] Pixabay: hit {hit.get('id')} has no HD files (height >= 720), checking next hit...")
                    continue

                tag_score = _pixabay_relevance(hit)
                print(f"[Clipper] Pixabay: downloading high-res clip ({best.get('width')}x{best.get('height')}) | tag_overlap={tag_score}...")
                r = requests.get(best["url"], stream=True, timeout=120)
                r.raise_for_status()
                with open(out_path, "wb") as fp:
                    for chunk in r.iter_content(chunk_size=65536):
                        fp.write(chunk)
                        
                if os.path.exists(out_path) and os.path.getsize(out_path) > 10_000:
                    if exclude_ids is not None and vid_id:
                        exclude_ids.add(vid_id)
                    return out_path
                    
            print(f"[Clipper] Pixabay: no usable file in results for '{query}'")
            return None
        except Exception as e:
            print(f"[Clipper] Pixabay error: {e}")
            return None

    # ── YouTube Fallback ──────────────────────────────────────────────────────

    def _search_youtube(self, query: str, out_path: str, timeout: int = 90, exclude_ids: set = None, visual_intent: str = None) -> str | None:
        """
        YouTube fallback via yt-dlp. Less reliable than Pexels but always available.
        Now queries top candidates and randomly picks one matching criteria to avoid repetitive clips.
        """
        # For specific entity/proper noun/event queries, we shouldn't restrict talking/news/interview keywords
        # because the actual clips we want of that person or event WILL contain those words.
        # But for generic queries, we can restrict them to keep it clean.
        if self._is_person_query(query):
            clean_query = f"{query} -twitch -gaming -meme -green_screen -istock -shutterstock -getty -watermark -stock -preview -demo -template -watermarked"
        else:
            clean_query = f"{query} -twitch -stream -overlay -meme -compilation -green_screen -reaction -gaming -istock -shutterstock -getty -watermark -stock -preview -demo -template -watermarked"
        
        # Step 1: Probe for top 15 search candidates to select from
        probe_cmd = [
            sys.executable, "-m", "yt_dlp",
            "--flat-playlist",
            "--print", "%(id)s | %(duration)s | %(uploader)s | %(title)s",
            f"ytsearch15:{clean_query}"
        ]
        probe_cmd = extend_with_cookies(probe_cmd)
        
        candidates = []
        import tempfile
        try:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="ignore") as temp_out:
                res = subprocess.run(probe_cmd, stdout=temp_out, stderr=subprocess.DEVNULL, timeout=20)
                temp_out.seek(0)
                stdout_text = temp_out.read()
            if res.returncode == 0:
                for line in stdout_text.strip().split("\n"):
                    if not line or "|" not in line:
                        continue
                    parts = line.split("|")
                    if len(parts) < 4:
                        continue
                    v_id = parts[0].strip()
                    dur_str = parts[1].strip()
                    v_uploader = parts[2].strip().lower()
                    v_title = "|".join(parts[3:]).strip().lower()
                    
                    try:
                        dur = float(dur_str) if (dur_str and dur_str != "NA") else 0.0
                    except ValueError:
                        dur = 0.0
                        
                    # Duration analysis: avoid extremely short clips or massive streams
                    if dur > 3600 or dur < 3:
                        print(f"[Clipper] Filtered out candidate: '{v_title}' (duration {dur}s out of bounds)")
                        continue
                        
                    # Deterministic Title & Keyword Analysis
                    negatives = [
                        "twitch", "stream", "meme", "green screen", 
                        "reaction", "gaming", "minecraft", 
                        "ai generated", "ai-generated", "talking head", 
                        "gta", "roblox", "let's play", "fortnite",
                        "streamer reaction", "motivational edit",
                        "meme edit", "fan edit", "gameplay", 
                        "grwm", "tutorial", "how to", "unboxing"
                    ]
                    
                    # Channel Name Analysis
                    bad_channels = [
                        "reacts", "gaming", "memes"
                    ]
                    
                    if any(neg in v_title for neg in negatives):
                        print(f"[Clipper] Filtered out candidate: '{v_title}' (title matched blacklist)")
                        continue
                        
                    if any(bad in v_uploader for bad in bad_channels):
                        print(f"[Clipper] Filtered out candidate: '{v_title}' (channel '{v_uploader}' matched blacklist)")
                        continue
                        
                    candidates.append({"id": v_id, "title": v_title})
        except Exception as e:
            print(f"[Clipper] YouTube candidate probe failed: {e}")

        # Deduplicate: filter out already-used YouTube video IDs
        if exclude_ids:
            candidates = [c for c in candidates if c["id"] not in exclude_ids]

        # Step 2: Download the selected or fallback video
        if candidates:
            # === Deterministic Authenticity Scoring System ===
            selected_id = candidates[0]["id"]
            if len(candidates) > 1:
                best_score = -999
                
                # Deterministic Keywords
                # We specifically allow news broadcasts, documentaries, and shorts because for Crime/Culture, 
                # these are often the ONLY places to find historical or context-specific footage.
                negative_keywords = ["podcast", "compilation", "reaction", "gaming", "explained", "trailer", "preview", "vlog", "interview", "shorts", "tiktok", "discussion", "talk show", "review", "commentary"]
                
                niche_lower = self._niche.lower() if self._niche else ""
                positive_keywords = ["raw", "raw footage", "real", "caught on camera"]
                if "crime" in niche_lower:
                    # Explicitly target raw footage
                    positive_keywords.extend(["bodycam", "interrogation", "cctv", "footage", "raw", "dashcam", "security camera", "surveillance", "news", "report", "live"])
                elif "cricket" in niche_lower:
                    positive_keywords.extend(["stadium", "match", "live", "action", "highlights", "fans", "six", "wicket"])

                
                for idx, c in enumerate(candidates):
                    title_lower = c["title"].lower()
                    score = 0
                    
                    # Penalize negative words
                    for neg in negative_keywords:
                        if neg in title_lower:
                            score -= 10
                            
                    # Boost positive words
                    for pos in positive_keywords:
                        if pos in title_lower:
                            score += 5

                    # [Phase 1B] Query-Title Word Overlap Scoring
                    # Most important signal: does the title actually contain the words we searched?
                    # e.g. query="Lars Mittank airport CCTV" should score a video titled
                    # "Lars Mittank Last Footage Varna Airport" much higher than "Crime Documentary 2024"
                    _STOP = {"the","a","an","in","on","at","for","by","of","and","or","is","to","with"}
                    q_words = set(query.lower().split()) - _STOP
                    t_words = set(title_lower.split())
                    overlap = q_words & t_words
                    overlap_ratio = len(overlap) / max(len(q_words), 1)
                    score += int(overlap_ratio * 20)  # 0-20 pts based on word match %

                    # Strong penalty if none of the query words are in the title (random unrelated news)
                    if len(overlap) == 0:
                        score -= 300

                    # Strong bonus for exact query as substring (e.g. name found verbatim in title)
                    if query.lower() in title_lower:
                        score += 150

                    # Slight penalty for lower search rank
                    score -= idx
                    
                    if score > best_score:
                        best_score = score
                        selected_id = c["id"]
                        
                print(f"[Clipper] Deterministic Scoring selected candidate ({selected_id}) with score {best_score}")

            else:
                print(f"[Clipper] Only 1 candidate found, skipping Scoring: {selected_id}")

            if exclude_ids is not None:
                exclude_ids.add(selected_id)  # Mark as used so future calls skip this video
            target_input = f"https://www.youtube.com/watch?v={selected_id}"
            print(f"[Clipper] Starting yt-dlp download for selected YouTube candidate: {selected_id} for '{query}'")
            is_culture = False
            if self._ctx:
                chan_name = getattr(self._ctx, 'channel_name', '') or ''
                display_name = getattr(self._ctx, 'display_name', '') or ''
                if "culture" in chan_name.lower() or "culture" in display_name.lower():
                    is_culture = True
            if "culture" in self._niche.lower() or "dating" in self._niche.lower():
                is_culture = True

            cmd = [
                sys.executable, "-m", "yt_dlp",
                target_input,
                "-f", "best[ext=mp4][height<=1080]/best[height<=1080]/best",
                "-o", out_path,
                "--quiet",
            ]
            cmd = extend_with_cookies(cmd)
        else:
            print(f"[Clipper] No clean YouTube candidates found via probe for '{query}'. Rejecting to prevent blind download.")
            return None
            
        import tempfile
        try:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="ignore") as temp_err:
                result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=temp_err, timeout=timeout)
                temp_err.seek(0)
                stderr_text = temp_err.read()
            if os.path.exists(out_path) and os.path.getsize(out_path) > 10_000:
                return out_path
                
            err_lower = stderr_text.lower()
            if "sign in" in err_lower or "bot" in err_lower or "cookie" in err_lower:
                if not getattr(self, "_cookie_alert_sent", False):
                    try:
                        from core.telegram_bot import send_message
                        send_message("⚠️ *YouTube Cookies Expired!*\nyt-dlp failed to download footage due to bot protection or invalid cookies.\n\nPlease use /cookies to update them.")
                        self._cookie_alert_sent = True
                    except Exception as e:
                        print(f"[Clipper] Failed to send Telegram alert: {e}")
            
            print(f"[Clipper] YouTube failed for '{query}': {stderr_text[:120]}")
        except subprocess.TimeoutExpired:
            print(f"[Clipper] YouTube timeout for '{query}'")
        return None

    # ── Clip Processing ───────────────────────────────────────────────────────

    def _process_clip(self, raw_path: str, out_path: str, duration: float) -> str | None:
        """
        Trim to `duration` seconds, scale to 1080×1920 (9:16), consistent 30fps.
        For longer YouTube clips (vlogs), bypasses the intro by seeking to a 15-second offset.
        """
        # Determine if we should bypass intro
        start_offset = 0.0
        try:
            # Probe raw video duration
            probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", raw_path]
            import tempfile
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="ignore") as temp_out:
                res = subprocess.run(probe_cmd, stdout=temp_out, stderr=subprocess.DEVNULL, timeout=5)
                temp_out.seek(0)
                val = temp_out.read().strip()
            raw_dur = float(val) if val else 0.0
            
            # If it's a long video (vlog/compilation fallback, usually > 60s)
            # Only skip intro if the video is long enough that the intro is likely not the main content
            if raw_dur > 60.0:
                # Seek to 20s offset to skip typical intro but not so far we miss the main action
                max_seek = raw_dur - duration - 2.0
                if max_seek > 20.0:
                    start_offset = 20.0
                elif max_seek > 5.0:
                    start_offset = max_seek
                if start_offset > 0:
                    print(f"[Clipper] Long vlog detected (dur={raw_dur:.1f}s) — seeking to {start_offset:.1f}s to bypass intro")
        except Exception as e:
            print(f"[Clipper] Non-fatal duration probe failed: {e}")

        # Probe raw video dimensions to handle landscape vs portrait properly
        width = 1920
        height = 1080
        try:
            probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", raw_path]
            import tempfile
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="ignore") as temp_out:
                res = subprocess.run(probe_cmd, stdout=temp_out, stderr=subprocess.DEVNULL, timeout=5)
                temp_out.seek(0)
                data = json.loads(temp_out.read())
            if "streams" in data and len(data["streams"]) > 0:
                width = int(data["streams"][0]["width"])
                height = int(data["streams"][0]["height"])
        except Exception as e:
            print(f"[Clipper] Non-fatal dimension probe failed: {e}")

        if width > height:
            print(f"[Clipper] Landscape source video ({width}x{height}) detected. Applying vertical blur stack.")
            vf = (
                "split[v1][v2];"
                "[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20[bg];"
                "[v2]scale=1080:-2[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
            )
        else:
            print(f"[Clipper] Portrait/Square source video ({width}x{height}) detected. Applying direct scale and crop.")
            vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"

        from config.settings import get_video_encoder_args
        encoder_args = get_video_encoder_args()
        
        # Build command with fast seeking if start_offset > 0
        cmd = ["ffmpeg", "-y"]
        if start_offset > 0.0:
            cmd += ["-ss", f"{start_offset:.3f}"]
        
        cmd += [
            "-i", raw_path,
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-r", "30",
            "-an",
        ] + encoder_args + [out_path]
        
        import tempfile
        try:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="ignore") as temp_err:
                result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=temp_err, timeout=120)
                temp_err.seek(0)
                stderr_text = temp_err.read()
                
            if result.returncode == 0 and os.path.exists(out_path):
                return out_path
            print(f"[Clipper] Process failed: {stderr_text[:200]}")
            return None
        except subprocess.TimeoutExpired:
            print(f"[Clipper] ffmpeg processing TIMED OUT after 120s for {raw_path}")
            return None


    # ── Main Entry ────────────────────────────────────────────────────────────

    def get_clip_for_story(self, script_data: dict,
                           output_name: str, recipe: dict = None) -> tuple[None, list[str]]:
        """
        Download CLIP_COUNT relevant clips.
        Returns (None, [clip1..clipN]) — bg_path always None (no gaming bg).

        Source priority per clip:
          1. Pexels (if API key configured) → portrait, relevant, reliable
          2. Alternative source (Pixabay or Pexels)
          3. YouTube via yt-dlp → less reliable, used as fallback
          4. Simplified query retry on any source
        """
        clips_dir = os.path.join(self._workspace_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)

        source    = "Pexels+Pixabay+YouTube" if PEXELS_API_KEY else "YouTube only"
        print(f"[Clipper] Source: {source}")

        queries   = self._get_clip_queries(script_data, recipe=recipe)

        # [V4 KINETIC UPDATE] No longer padding queries. We download exactly 1 clip per scene.
        # The V4 Kinetic assembler will mathematically slice this single clip into micro-cuts.
        print(f"[Clipper] Sourcing exactly {len(queries)} base clips for kinetic splicing.")

        # Move channel parsing UP so we can use is_crime safely
        is_crime = False
        is_stoic = False
        is_cricket = False
        is_culture = False
        if self._ctx:
            chan_name = getattr(self._ctx, 'channel_name', '') or ''
            display_name = getattr(self._ctx, 'display_name', '') or ''
            if "crime" in chan_name.lower() or "crime" in display_name.lower():
                is_crime = True
            if "stoic" in chan_name.lower() or "stoic" in display_name.lower():
                is_stoic = True
            if "cricket" in chan_name.lower() or "cricket" in display_name.lower():
                is_cricket = True
            if "culture" in chan_name.lower() or "culture" in display_name.lower():
                is_culture = True

        if "culture" in self._niche.lower() or "dating" in self._niche.lower():
            is_culture = True
        elif "crime" in self._niche.lower() or "mystery" in self._niche.lower():
            is_crime = True
        elif "stoic" in self._niche.lower() or "discipline" in self._niche.lower():
            is_stoic = True
        elif "cricket" in self._niche.lower() or "sports" in self._niche.lower():
            is_cricket = True

        num_clips = len(queries)
        # We need a large buffer because the V4 engine slices the clip sequentially
        clip_dur  = (self._video_duration / max(1, num_clips)) + 8

        sections        = ["hook", "body1", "body2", "body3", "body4", "cta"]
        processed_clips = []
        _used_yt_ids = set()  # Track YouTube video IDs used this run to prevent duplicate clip sources
        
        # Load Global Clip Ledger (Sliding window of last 150 stock IDs)
        ledger_path = os.path.join(self._workspace_dir, "used_clips.json")
        global_ledger = []
        if os.path.exists(ledger_path):
            try:
                with open(ledger_path, "r", encoding="utf-8") as f:
                    global_ledger = json.load(f)
            except Exception:
                pass
        _used_stock_ids = set(global_ledger)

        # Retrieval Validation Logging setup
        validation_log_path = os.path.join(self._workspace_dir, "logs", f"{output_name}_retrieval_validation.json")
        os.makedirs(os.path.dirname(validation_log_path), exist_ok=True)
        all_validation_events = []

        for i, q_obj in enumerate(queries):
            query    = q_obj["query"]
            src_pref = q_obj.get("source", "pexels")
            pex_query = q_obj.get("pexels_query", query)
            visual_intent = q_obj.get("visual_intent", "")
            narration = q_obj.get("narration", "")
            section  = sections[i] if i < len(sections) else f"clip{i}"
            out_path = os.path.join(clips_dir, f"{output_name}_clip{i}.mp4")

            print(f"[Clipper] [{section}] Preferred: [{src_pref.upper()}] '{query}'")

            # [V24 ZERO-TOKEN OPTIMIZATION] Use the native 4-tier fallback queries provided by the Script LLM
            tiered_queries = {
                "tier1": q_obj.get("tier1_query", query),
                "tier2": q_obj.get("tier2_query", query),
                "tier3": q_obj.get("tier3_query", query),
                "tier4": q_obj.get("tier4_query", query)
            }
            print(f"[Clipper] [{section}] Generated Tiers:")
            print(f"  Tier 1 (Exact): '{tiered_queries['tier1']}'")
            print(f"  Tier 2 (Context): '{tiered_queries['tier2']}'")
            print(f"  Tier 3 (Event): '{tiered_queries['tier3']}'")
            print(f"  Tier 4 (Atmosphere): '{tiered_queries['tier4']}'")
            # Prioritize candidate sources to try
            candidate_sources = []
            
            # YouTube-first routing override for Crime and Cricket channels:
            if is_crime or is_cricket:
                candidate_sources.append(("YouTube", "youtube"))
                candidate_sources.append(("Pexels", "pexels"))
            else:
                if src_pref == "youtube":
                    candidate_sources.append(("YouTube", "youtube"))
                elif src_pref == "pexels":
                    candidate_sources.append(("Pexels", "pexels"))
                elif src_pref == "image" or src_pref == "news":
                    candidate_sources.append(("Authentic Image", "image"))

            # Fill in fallbacks (but DO NOT fallback to YouTube if they explicitly want an authentic image)
            if ("YouTube", "youtube") not in candidate_sources and src_pref != "image" and src_pref != "news":
                candidate_sources.append(("YouTube", "youtube"))
                
            if ("Pexels", "pexels") not in candidate_sources:
                if not is_crime and not is_cricket:
                    candidate_sources.append(("Pexels", "pexels"))
                    candidate_sources.append(("Pixabay", "pixabay"))

            if ("Authentic Image", "image") not in candidate_sources:
                candidate_sources.append(("Authentic Image", "image"))
                
            # Remove banned sources (as defined in channel config)
            banned_sources = self._ctx.raw_config.get("visuals", {}).get("banned_sources", [])
            candidate_sources = [(name, stype) for (name, stype) in candidate_sources if stype not in banned_sources]

            # Deduplicate the list while preserving order
            seen_sources = set()
            dedup_candidates = []
            for c in candidate_sources:
                if c not in seen_sources:
                    seen_sources.add(c)
                    dedup_candidates.append(c)
            candidate_sources = dedup_candidates

            processed = None
            
            # Loop over candidate sources and try the 4 tiers of fallback queries on each source
            for source_name, source_type in candidate_sources:
                if processed:
                    break

                print(f"[Clipper] [{section}] Trying source: {source_name}")

                tiers_to_try = [
                    ("tier1", "Exact Match"),
                    ("tier2", "Entity Context Match"),
                    ("tier3", "Event Match"),
                    ("tier4", "Atmosphere Match")
                ]

                for tier_key, tier_name in tiers_to_try:
                    current_query = tiered_queries[tier_key]
                    print(f"[Clipper] [{section}]   Trying {tier_name} (Query: '{current_query}') on {source_name}...")

                    attempt_raw_path = os.path.join(self._workspace_dir, f"{output_name}_raw_{section}_{source_type}_{tier_key}.mp4")
                    raw_clip = None

                    try:
                        if source_type == "image":
                            scraper = ImageScraper(self._workspace_dir)
                            raw_clip = scraper.get_image_clip(current_query, attempt_raw_path, clip_dur, is_crime=is_crime)
                        elif source_type == "youtube":
                            yt_q = current_query
                            if tier_key != "tier1":
                                if is_cricket:
                                    yt_q = " ".join(current_query.split()[:3]) + " highlights"
                                elif is_crime:
                                    yt_q = current_query + " news"
                            raw_clip = self._search_youtube(yt_q, attempt_raw_path, exclude_ids=_used_yt_ids, visual_intent=visual_intent)
                        elif source_type == "pexels":
                            raw_clip = self._search_pexels(current_query, attempt_raw_path, clip_dur, exclude_ids=_used_stock_ids)
                        elif source_type == "pixabay":
                            raw_clip = self._search_pixabay(current_query, attempt_raw_path, clip_dur, exclude_ids=_used_stock_ids)
                    except Exception as e:
                        print(f"[Clipper] Retrieval error on {source_name} for '{current_query}': {e}")
                        raw_clip = None

                    if not raw_clip or not os.path.exists(raw_clip) or os.path.getsize(raw_clip) < 10_000:
                        print(f"[Clipper] [{section}]     No asset retrieved from {source_name} for '{current_query}'")
                        all_validation_events.append({
                            "original_query": query,
                            "query": current_query,
                            "section": section,
                            "source": source_name,
                            "asset": None,
                            "score": 0,
                            "accepted": False,
                            "reason": f"Retrieval empty or download failed on {source_name}",
                            "narration": narration,
                            "visual_intent": visual_intent,
                            "tier": tier_key,
                            "tier_name": tier_name
                        })
                        continue

                    # Validate the retrieved asset
                    print(f"[Clipper] [{section}]     Validating retrieved asset: {raw_clip}")
                    val_res = validate_asset(raw_clip, current_query, narration, visual_intent, display_name)

                    # Log the event
                    all_validation_events.append({
                        "original_query": query,
                        "query": current_query,
                        "section": section,
                        "source": source_name,
                        "asset": raw_clip,
                        "score": val_res.get("score", 0),
                        "accepted": val_res.get("accept", False),
                        "reason": val_res.get("reason", "Unknown validation error"),
                        "narration": narration,
                        "visual_intent": visual_intent,
                        "tier": tier_key,
                        "tier_name": tier_name
                    })

                    if val_res.get("accept", False):
                        processed = self._process_clip(raw_clip, out_path, clip_dur)
                        if processed:
                            print(f"[Clipper] [{section}] [VALIDATED OK] {source_name} ({tier_name}) - Score {val_res.get('score')}")
                            break
                    else:
                        print(f"[Clipper] [{section}] [REJECTED] {source_name} ({tier_name}) - Score {val_res.get('score')} - Reason: {val_res.get('reason')}")
                        try:
                            os.remove(raw_clip)
                        except Exception:
                            pass

            if not processed:
                # ── All Sourcing Failed or Rejected ──
                print(f"[Clipper] [{section}] [FAIL] REJECTED ALL — Attempting Global Fallback mechanism to prevent black screens.")
                
                # 0. Try Context-Aware Stock Fallback First
                # Clean up the query string (use short query and truncate to max 3 words for Pexels)
                context_query = " ".join(query.lower().split()[:3])
                for filler in ["vlog", "candid", "footage", "video", "clip", "showing", "background"]:
                    context_query = context_query.replace(filler, "")
                context_query = context_query.strip()
                
                if context_query and len(context_query) > 3:
                    attempt_raw_path = os.path.join(self._workspace_dir, f"{output_name}_raw_{section}_context_fallback.mp4")
                    raw_clip = self._search_pexels(context_query, attempt_raw_path, clip_dur, exclude_ids=_used_stock_ids)
                    if not raw_clip:
                        raw_clip = self._search_pixabay(context_query, attempt_raw_path, clip_dur, exclude_ids=_used_stock_ids)
                    
                    if raw_clip and os.path.exists(raw_clip):
                        processed = self._process_clip(raw_clip, out_path, clip_dur)
                        if processed:
                            print(f"[Clipper] [{section}] [FALLBACK OK] Used context-aware fallback '{context_query}'")
                
                if not processed:
                    # 0.5. Try concrete physical subject fallback (last 2-3 words of query)
                    q_words = query.lower().split()
                    concrete_query = None
                    if len(q_words) >= 2:
                        concrete_query = " ".join(q_words[-3:]) if len(q_words) >= 3 else " ".join(q_words[-2:])
                        for filler in ["vlog", "candid", "footage", "video", "clip", "showing", "background"]:
                            concrete_query = concrete_query.replace(filler, "")
                        concrete_query = concrete_query.strip()
                    
                    if concrete_query and len(concrete_query) > 3:
                        attempt_raw_path = os.path.join(self._workspace_dir, f"{output_name}_raw_{section}_concrete_fallback.mp4")
                        print(f"[Clipper] [{section}] Trying concrete physical subject fallback: '{concrete_query}'")
                        raw_clip = self._search_pexels(concrete_query, attempt_raw_path, clip_dur, exclude_ids=_used_stock_ids)
                        if not raw_clip:
                            raw_clip = self._search_pixabay(concrete_query, attempt_raw_path, clip_dur, exclude_ids=_used_stock_ids)
                        
                        if raw_clip and os.path.exists(raw_clip):
                            processed = self._process_clip(raw_clip, out_path, clip_dur)
                            if processed:
                                print(f"[Clipper] [{section}] [FALLBACK OK] Used concrete subject fallback '{concrete_query}'")
                
                if not processed:
                    # 1. Try Niche-Specific Stock Fallback (Generic)
                    fallback_list = _get_fallback_queries(self._niche)
                    # We can't shuffle in place easily without copying, so we sample random item
                    import copy
                    import random
                    fallback_list_shuffled = copy.deepcopy(fallback_list)
                    random.shuffle(fallback_list_shuffled)
                    
                    for fq in fallback_list_shuffled:
                        attempt_raw_path = os.path.join(self._workspace_dir, f"{output_name}_raw_{section}_global_fallback.mp4")
                        raw_clip = self._search_pexels(fq["pexels_query"], attempt_raw_path, clip_dur, exclude_ids=_used_stock_ids)
                        if not raw_clip:
                            raw_clip = self._search_pixabay(fq["pexels_query"], attempt_raw_path, clip_dur, exclude_ids=_used_stock_ids)
                        
                        if raw_clip and os.path.exists(raw_clip):
                            processed = self._process_clip(raw_clip, out_path, clip_dur)
                            if processed:
                                print(f"[Clipper] [{section}] [FALLBACK OK] Used generic fallback '{fq['pexels_query']}'")
                                break
                            
                # If all fallbacks fail, raise an error to trigger Story B instead of using garbage clips.
                if not processed:
                    raise RuntimeError(f"[Clipper] Critical Failure: Cannot source clip for section {section}. All fallbacks exhausted.")
            else:
                processed_clips.append(processed)

        # Merge local youtube IDs into the global ledger so they persist across runs!
        _used_stock_ids.update(_used_yt_ids)

        # Write final validation events log
        with open(validation_log_path, "w", encoding="utf-8") as f:
            json.dump(all_validation_events, f, indent=2)
        print(f"[Clipper] Wrote validation logs to {validation_log_path}")

        # Update and save Global Clip Ledger (keep last 150 clips)
        new_ledger = list(_used_stock_ids)[-150:]
        try:
            with open(ledger_path, "w", encoding="utf-8") as f:
                json.dump(new_ledger, f)
            print(f"[Clipper] Updated global clip ledger ({len(new_ledger)} clips tracked).")
        except Exception as e:
            print(f"[Clipper] Failed to update global clip ledger: {e}")

        print(f"[Clipper] Got {len(processed_clips)}/{num_clips} clips")
        return (None, processed_clips)
