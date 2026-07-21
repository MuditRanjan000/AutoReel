"""
execution/review_video.py
Runs the TEXT quality gate against a completed pipeline run.
Reads the script, .ass subtitle file, and pipeline log, then uses
Groq (via generate_with_rotation) to score the script and surface
actionable issues. This is Step 5.2 — BEFORE the AI video reviewer.

Usage:
    python execution/review_video.py <run_id>
    python execution/review_video.py 20260515_221927

Output:
    Prints the quality review to console.
    Saves review JSON to output/logs/<run_id>_review.json
"""

import sys
import os
import json
import glob
import re
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.gemini_client import generate_with_rotation
from config.settings import OUTPUT_DIR, LOG_DIR, VIDEO_DURATION_SECONDS, QUALITY_SCORE_THRESHOLD

SKILL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "text-quality-gate", "SKILL.md"
)


def parse_ass_time(t_str: str) -> float:
    parts = t_str.strip().split(':')
    if len(parts) != 3:
        return 0.0
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s


def clean_ass_text(text: str) -> str:
    cleaned = re.sub(r'\{[^\}]+\}', '', text)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


def run_programmatic_checks(run_data: dict) -> list:
    issues = []
    run_id = run_data.get("run_id", "")
    summary = run_data.get("summary", {})
    script_data = summary.get("script", {})
    
    full_script = script_data.get("full_script", "")
    hook = script_data.get("hook", "")
    title = script_data.get("title", summary.get("title", ""))
    search_queries = script_data.get("search_queries", [])
    log_excerpt = run_data.get("log_excerpt", "")
    ass_contents = run_data.get("ass_contents", "")

    # --- Hook checks ---
    if hook:
        soft_openers = ["hey", "did you know", "today we", "in this video", "welcome back"]
        hook_lower = hook.lower().strip()
        for opener in soft_openers:
            if hook_lower.startswith(opener):
                issues.append({
                    "severity": "CRITICAL",
                    "category": "Category 1: Hook Strength",
                    "description": f"Hook starts with a soft opener: '{opener}'",
                    "fix": "Remove the soft opener and start with the core hook immediately."
                })
                break
        hook_words = [w for w in re.findall(r"[A-Za-z0-9']+", hook)]
        if len(hook_words) > 30:
            issues.append({
                "severity": "INFO",
                "category": "Category 1: Hook Strength",
                "description": f"The hook is longer than 30 words ({len(hook_words)} words)",
                "fix": "Shorten the hook under 30 words."
            })

    # --- Dynamic Duration Extraction ---
    actual_duration = 0.0
    if ass_contents and "Dialogue:" in ass_contents:
        try:
            for line in reversed(ass_contents.splitlines()):
                if line.startswith("Dialogue:"):
                    parts = line.split(",", 9)
                    if len(parts) >= 3:
                        actual_duration = max(actual_duration, parse_ass_time(parts[2]))
        except Exception:
            pass

    # --- Script Length checks ---
    if full_script:
        script_words = [w for w in re.findall(r"[A-Za-z0-9']+", full_script)]
        word_count = len(script_words)
        min_words = int(VIDEO_DURATION_SECONDS * 1.8)
        max_words = int(VIDEO_DURATION_SECONDS * 2.3)
        
        if actual_duration > 0:
            if actual_duration > VIDEO_DURATION_SECONDS + 2:
                issues.append({
                    "severity": "CRITICAL",
                    "category": "Category 2: Script Length",
                    "description": f"Actual voiceover duration ({actual_duration:.1f}s) exceeds maximum allowed ({VIDEO_DURATION_SECONDS}s).",
                    "fix": f"Shorten script under {max_words} words to fit within limits."
                })
            elif actual_duration < VIDEO_DURATION_SECONDS * 0.7:
                issues.append({
                    "severity": "INFO",
                    "category": "Category 2: Script Length",
                    "description": f"Actual voiceover duration ({actual_duration:.1f}s) is significantly under target.",
                    "fix": f"Expand script to improve pacing and retention."
                })
        else:
            if word_count < (min_words - 15):
                issues.append({
                    "severity": "CRITICAL",
                    "category": "Category 2: Script Length",
                    "description": f"Script is extremely short ({word_count} words) for target duration of {VIDEO_DURATION_SECONDS}s (expected at least {min_words} words).",
                    "fix": f"Add more details/facts/analysis to reach the target range of {min_words}-{max_words} words."
                })
            elif word_count < min_words:
                issues.append({
                    "severity": "INFO",
                    "category": "Category 2: Script Length",
                    "description": f"Script is slightly short ({word_count} words) for target duration of {VIDEO_DURATION_SECONDS}s (expected at least {min_words} words).",
                    "fix": f"Expand script details/body to reach {min_words}-{max_words} words."
                })
            elif max_words < word_count <= (max_words + 15):
                issues.append({
                    "severity": "INFO",
                    "category": "Category 2: Script Length",
                    "description": f"Script is slightly long ({word_count} words) for target duration of {VIDEO_DURATION_SECONDS}s (expected under {max_words} words).",
                    "fix": f"Shorten the script slightly under {max_words} words."
                })
            elif word_count > (max_words + 15):
                issues.append({
                    "severity": "CRITICAL",
                    "category": "Category 2: Script Length",
                    "description": f"Script is too long ({word_count} words) — will exceed the 60s Shorts limit.",
                    "fix": f"Shorten script under {max_words} words to guarantee it fits under 60 seconds."
                })

    # --- Sentence Length checks ---
    if full_script:
        cleaned_script = re.sub(r'\s+', ' ', full_script)
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', cleaned_script) if s.strip()]
        for sentence in sentences:
            sentence_words = [w for w in re.findall(r"[A-Za-z0-9']+", sentence)]
            if len(sentence_words) > 25:
                issues.append({
                    "severity": "INFO",
                    "category": "Category 3: Sentence Length",
                    "description": f"Sentence exceeds 25 words: \"{sentence}\" ({len(sentence_words)} words)",
                    "fix": "Split the sentence into two shorter, snappier sentences."
                })

    # --- Caption Timing checks (ASS file review) ---
    if ass_contents and "Dialogue:" in ass_contents:
        events = []
        for line in ass_contents.splitlines():
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) >= 10:
                    start = parse_ass_time(parts[1])
                    end = parse_ass_time(parts[2])
                    text = parts[9]
                    clean_text = clean_ass_text(text)
                    events.append({
                        "start": start,
                        "end": end,
                        "clean_text": clean_text
                    })
        
        # Merge consecutive events with identical clean_text (representing word highlights on the same card)
        merged_events = []
        for event in events:
            if merged_events and merged_events[-1]["clean_text"] == event["clean_text"]:
                merged_events[-1]["end"] = max(merged_events[-1]["end"], event["end"])
            else:
                merged_events.append({
                    "start": event["start"],
                    "end": event["end"],
                    "clean_text": event["clean_text"]
                })
        
        for idx, event in enumerate(merged_events):
            dur = event["end"] - event["start"]
            if dur < 0.15:
                issues.append({
                    "severity": "WARNING",
                    "category": "Category 4: Caption Timing (ASS File Review)",
                    "description": f"Caption duration too fast: {dur:.2f}s for text: \"{event['clean_text']}\"",
                    "fix": "Extend the duration to at least 0.15s."
                })
            
            event_words = event["clean_text"].split()
            if len(event_words) > 4:
                issues.append({
                    "severity": "WARNING",
                    "category": "Category 4: Caption Timing (ASS File Review)",
                    "description": f"Caption event has > 4 words ({len(event_words)} words): \"{event['clean_text']}\"",
                    "fix": "Split this caption block into smaller chunks with fewer words."
                })
                
            if idx + 1 < len(merged_events):
                next_event = merged_events[idx + 1]
                gap = next_event["start"] - event["end"]
                if gap > 0.8:
                    issues.append({
                        "severity": "WARNING",
                        "category": "Category 4: Caption Timing (ASS File Review)",
                        "description": f"Time gap between captions is too large ({gap:.2f}s) at {event['end']:.2f}s",
                        "fix": "Bridge the gap by stretching caption duration or adding visual pacing."
                    })
                elif gap < -0.001:  # overlapping
                    issues.append({
                        "severity": "CRITICAL",
                        "category": "Category 4: Caption Timing (ASS File Review)",
                        "description": f"Captions overlap: \"{event['clean_text']}\" ends at {event['end']:.2f}s, next starts at {next_event['start']:.2f}s",
                        "fix": "Adjust start/end subtitle parameters to prevent timeline collision."
                    })

    # --- Title Quality checks ---
    if title:
        if len(title) > 60:
            issues.append({
                "severity": "WARNING",
                "category": "Category 6: Title Quality",
                "description": f"Title is longer than 60 characters ({len(title)} characters): \"{title}\"",
                "fix": "Shorten the title under 60 characters."
            })
        if "||" in title or "**" in title or title.count("!") > 1:
            issues.append({
                "severity": "WARNING",
                "category": "Category 6: Title Quality",
                "description": f"Title contains spammy characters or multiple exclamation marks: \"{title}\"",
                "fix": "Clean up title formatting to avoid looking spammy."
            })

    # --- Visual Diversity checks ---
    if search_queries:
        if len(search_queries) < 4:
            issues.append({
                "severity": "WARNING",
                "category": "Category 8: Visual Diversity",
                "description": f"Fewer than 4 search queries found ({len(search_queries)} queries)",
                "fix": "Add more search queries to increase B-roll visual diversity."
            })
        from collections import Counter
        counts = Counter(search_queries)
        repeated = [q for q, c in counts.items() if c > 2]
        if repeated:
            issues.append({
                "severity": "WARNING",
                "category": "Category 8: Visual Diversity",
                "description": f"Search queries repeated more than twice: {repeated}",
                "fix": "Replace duplicate queries with unique ones to avoid visual fatigue."
            })

    # --- Pipeline Log checks ---
    if log_excerpt:
        if "Merge error" in log_excerpt or "returncode != 0" in log_excerpt:
            issues.append({
                "severity": "CRITICAL",
                "category": "Category 5: Pipeline Log Review",
                "description": "Pipeline logs detect a command failure or merge error.",
                "fix": "Review the full execution logs to fix build errors."
            })
        if "No clip found" in log_excerpt or "Download failed" in log_excerpt:
            issues.append({
                "severity": "WARNING",
                "category": "Category 5: Pipeline Log Review",
                "description": "B-roll clips download failed, causing static visual overrides.",
                "fix": "Ensure Pexels search query yields valid download results."
            })
        if "Text overlay error" in log_excerpt:
            issues.append({
                "severity": "INFO",
                "category": "Category 5: Pipeline Log Review",
                "description": "Branding watermark text overlay failed.",
                "fix": "Check host OS system font installation path mappings."
            })
        if "bgm.mp3" in log_excerpt and ("failure" in log_excerpt.lower() or "error" in log_excerpt.lower() or "not found" in log_excerpt.lower()):
            issues.append({
                "severity": "WARNING",
                "category": "Category 5: Pipeline Log Review",
                "description": "Background music failed to download.",
                "fix": "Check that Music Director is fetching valid copyright-free tracks."
            })

    # --- Pronoun Phrasing check ---
    if full_script:
        pronoun_pattern1 = r"\b([A-Z][a-z]+)\s+(?:or|and)\s+(he|she|I|me|we|they|him|her|us|them|you)\b"
        pronoun_pattern2 = r"\b(he|she|I|me|we|they|him|her|us|them|you)\s+(?:or|and)\s+([A-Z][a-z]+)\b"
        
        matches1 = re.findall(pronoun_pattern1, full_script)
        matches2 = re.findall(pronoun_pattern2, full_script, re.IGNORECASE)
        
        common_words = {"But", "Now", "If", "When", "And", "Or", "Then", "So", "He", "She", "I", "We", "They", "You", "It", "Who", "What", "How", "Why", "Where", "Because", "Although", "While"}
        
        for noun, pronoun in matches1:
            if noun not in common_words:
                issues.append({
                    "severity": "CRITICAL",
                    "category": "Category 10: Grammar and Pronoun Phrasing",
                    "description": f"Improper proper noun and pronoun combination found: '{noun} or/and {pronoun}'",
                    "fix": f"Rewrite to use either both names (e.g., '{noun} or the other player') or a collective reference (e.g., 'either of them')."
                })
        for pronoun, noun in matches2:
            if noun not in common_words:
                issues.append({
                    "severity": "CRITICAL",
                    "category": "Category 10: Grammar and Pronoun Phrasing",
                    "description": f"Improper pronoun and proper noun combination found: '{pronoun} or/and {noun}'",
                    "fix": f"Rewrite to use either both names or a collective reference."
                })

    # --- Word Repetition check ---
    if full_script:
        words = [w.lower() for w in re.findall(r"[A-Za-z]+", full_script)]
        stop_words = {
            'the', 'and', 'that', 'this', 'with', 'from', 'their', 'they', 'them', 'have', 'been', 'were', 
            'will', 'would', 'should', 'could', 'what', 'about', 'there', 'their', 'your', 'more', 'some', 
            'other', 'here', 'here\'s', 'theres', 'about', 'just', 'only', 'than', 'then', 'went', 'were'
        }
        for i in range(len(words)):
            w = words[i]
            if len(w) < 4 or w in stop_words:
                continue
            for j in range(i + 1, min(i + 7, len(words))):
                if w == words[j]:
                    issues.append({
                        "severity": "WARNING",
                        "category": "Category 11: Vocabulary Repetition",
                        "description": f"Content word '{w}' is repeated in close proximity (separated by only {j - i - 1} words)",
                        "fix": f"Replace the second occurrence of '{w}' with a synonym to keep the script engaging."
                    })
                    break

    # --- Factuality and Accuracy checks ---
    if full_script and summary.get("story"):
        story_data = summary.get("story", {})
        story_title = story_data.get("title", "") if isinstance(story_data, dict) else ""
        story_summary = story_data.get("summary", "") if isinstance(story_data, dict) else ""
        story_text = (story_title + " " + story_summary).lower()
        
        # Convert spelled-out numbers to digits in both text streams to prevent false-positive mismatches
        number_map = {
            "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
            "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"
        }
        
        def normalize_numbers(text):
            text = text.lower()
            text = text.replace(" point ", ".")
            text = text.replace(" dot ", ".")
            for word, val in number_map.items():
                text = re.sub(rf"\b{word}\b", val, text)
            return text

        normalized_script = normalize_numbers(full_script)
        normalized_story = normalize_numbers(story_text)

        # Extract numbers, preserving commas and decimals (e.g. 10,000 or 1.5)
        script_numbers = [n.replace(",", "") for n in re.findall(r"\b\d+(?:[.,]\d+)*\b", normalized_script)]
        story_numbers = set([n.replace(",", "") for n in re.findall(r"\b\d+(?:[.,]\d+)*\b", normalized_story)])
        
        for num in script_numbers:
            if num not in story_numbers:
                if num in {"1", "24", "0"}:
                    continue
                if num.isdigit() and 2000 <= int(num) <= 2050:
                    continue
                issues.append({
                    "severity": "CRITICAL",
                    "category": "Category 12: Factuality and Hallucinations",
                    "description": f"Hallucinated number '{num}' found in script that does not exist in the source story headline or summary.",
                    "fix": "Remove the fabricated number and refer to the facts generally (e.g. 'dropped catches' instead of '2 dropped catches')."
                })


        # --- Duplicate Thumbnail check ---
        thumbnail_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "output", "thumbnails", f"{run_id}_thumbnail.jpg"
        )
        if os.path.exists(thumbnail_path):
            current_size = os.path.getsize(thumbnail_path)
            thumbnails_dir = os.path.dirname(thumbnail_path)
            if os.path.exists(thumbnails_dir):
                for path in glob.glob(os.path.join(thumbnails_dir, "*.jpg")):
                    if os.path.basename(path) != f"{run_id}_thumbnail.jpg" and os.path.basename(path) != f"{run_id}_thumb.jpg":
                        if os.path.getsize(path) == current_size:
                            if current_size > 5000:
                                mtime = os.path.getmtime(path)
                                if time.time() - mtime < 10 * 86400: # 10 days
                                    prev_run = os.path.basename(path).replace("_thumbnail.jpg", "").replace("_thumb.jpg", "")
                                    issues.append({
                                        "severity": "CRITICAL",
                                        "category": "Category 13: Duplicate Thumbnail",
                                        "description": f"Duplicate thumbnail detected: The generated thumbnail is identical to a previously generated thumbnail from run {prev_run} (size: {current_size} bytes).",
                                        "fix": "Shuffle B-roll query selection or clear search cache to force a fresh visual frame."
                                    })
                                    break

        # --- BGM Lyric check ---
        bgm_track_id = summary.get("script", {}).get("bgm_track_id") or summary.get("bgm_track_id")
        if not bgm_track_id and "summary" in run_data:
            bgm_track_id = run_data.get("summary", {}).get("bgm_track_id")
            
        if not bgm_track_id:
            bgm_meta_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "output", "videos", f"{run_id}_bgm.mp3.json"
            )
            if os.path.exists(bgm_meta_path):
                try:
                    with open(bgm_meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        bgm_track_id = meta.get("track_id")
                except Exception:
                    pass

        if bgm_track_id:
            LYRIC_TRACK_IDS = [
                "n8X9_MgEdCg", # TheFatRat - Unity (Vocal)
                "0MQpnVUYk0A", # Janji - Heroes Tonight (Vocal)
                "jK2aIUmmdP4", # Different Heaven - My Heart (Vocal chops)
            ]
            if any(lyric_id in str(bgm_track_id) for lyric_id in LYRIC_TRACK_IDS):
                issues.append({
                    "severity": "CRITICAL",
                    "category": "Category 14: Vocal Background Music",
                    "description": f"BGM track ID '{bgm_track_id}' contains vocals or lyrics.",
                    "fix": "Remove the vocal track from the BGM pool and replace it with a strictly instrumental version."
                })

    return issues


def clean_llm_issues(llm_issues: list, prog_issues: list) -> list:
    cleaned = []
    
    prog_has_soft_opener = any(i["category"] == "Category 1: Hook Strength" and "soft opener" in i["description"].lower() for i in prog_issues)
    prog_has_hook_len = any(i["category"] == "Category 1: Hook Strength" and "longer than 15 words" in i["description"].lower() for i in prog_issues)
    
    prog_has_title_len = any(i["category"] == "Category 6: Title Quality" and "longer than 60 characters" in i["description"].lower() for i in prog_issues)
    prog_has_title_symbols = any(i["category"] == "Category 6: Title Quality" and "spammy" in i["description"].lower() for i in prog_issues)
    
    for issue in llm_issues:
        category = issue.get("category", "")
        desc = issue.get("description", "").lower()
        fix = issue.get("fix", "").lower()
        
        if any(term in desc for term in ["no critical", "no errors", "no action needed", "no issues found", "none found"]) or \
           any(term in fix for term in ["no action needed", "none", "n/a"]):
            continue
            
        cat_lower = category.lower()
        if "script length" in cat_lower or "category 2" in cat_lower:
            continue
        if "sentence length" in cat_lower or "category 3" in cat_lower:
            continue
        if "caption timing" in cat_lower or "ass file" in cat_lower or "category 4" in cat_lower:
            continue
        if "pipeline log" in cat_lower or "category 5" in cat_lower:
            continue
        if "visual diversity" in cat_lower or "category 8" in cat_lower:
            continue
            
        if "hook strength" in cat_lower or "category 1" in cat_lower:
            if "soft opener" in desc and not prog_has_soft_opener:
                continue
            if "words long" in desc or "exceeds" in desc or "limit" in desc:
                if not prog_has_hook_len:
                    continue
                    
        if "title quality" in cat_lower or "category 6" in cat_lower:
            if ("character" in desc or "char" in desc or "length" in desc or "too long" in desc or "truncated" in desc or "long title" in desc) and not prog_has_title_len:
                continue
            if ("spammy" in desc or "exclamation" in desc or "symbol" in desc) and not prog_has_title_symbols:
                continue
                
        cleaned.append(issue)
        
    return cleaned


def load_skill() -> str:
    try:
        with open(SKILL_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print("[Reviewer] WARNING: skill file not found at", SKILL_PATH)
        return ""


def load_run_data(run_id: str) -> dict:
    """Load script, subtitle file, and log for a given run ID."""
    data = {"run_id": run_id}

    # Script summary
    summary_path = os.path.join(LOG_DIR, f"{run_id}_summary.json")
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            data["summary"] = json.load(f)

    # ASS subtitle file
    ass_path = os.path.join(OUTPUT_DIR, "videos", f"{run_id}_voice.ass")
    if os.path.exists(ass_path):
        with open(ass_path, "r", encoding="utf-8") as f:
            data["ass_contents"] = f.read()
    else:
        data["ass_contents"] = "(subtitle file not found)"

    # Pipeline log (last 100 lines relevant to this run)
    log_path = os.path.join(LOG_DIR, "pipeline.log")
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Find lines relevant to this run
        run_lines = [l for l in lines if run_id in l]
        data["log_excerpt"] = "".join(run_lines[-50:])
    else:
        data["log_excerpt"] = "(log file not found)"

    # Script JSON (from script_summary or reconstruct)
    if "summary" in data:
        sum_data = data["summary"]
        script_data = sum_data.get("script", {})
        data["title"] = script_data.get("title", sum_data.get("title", ""))
        data["hook"] = script_data.get("hook", "")
        data["cta"] = script_data.get("cta", "")
        data["story"] = sum_data.get("story", {}).get("title", sum_data.get("story", ""))

    return data


def review(run_id: str) -> dict:
    skill = load_skill()
    run_data = load_run_data(run_id)

    if not skill:
        print("[Reviewer] Cannot review without skill file.")
        return {}

    prompt = f"""{skill}

---
## Video to Review

Run ID: {run_id}
Script Data:
{json.dumps(run_data.get('summary', {}), indent=2)}

### Pipeline Log Excerpt
{run_data.get('log_excerpt', '(none)')}

### Subtitle File (.ass) — First 3000 chars
{run_data.get('ass_contents', '(none)')[:3000]}

---
Apply the checklist from the skill above. Output ONLY the JSON review object described in the skill. No preamble.
"""

    try:
        raw = generate_with_rotation(prompt)  # auto-rotates keys on 429
        raw = raw.strip().replace("```json", "").replace("```", "")
        review_data = json.loads(raw)
        
        # --- Mathematical Scoring Audit with Programmatic Verification ---
        prog_issues = run_programmatic_checks(run_data)
        llm_issues = review_data.get("issues", [])
        cleaned_llm = clean_llm_issues(llm_issues, prog_issues)
        
        valid_issues = prog_issues + cleaned_llm
        
        base_score = 100
        deductions = []
        final_issues = []
        
        for issue in valid_issues:
            desc = issue.get("description", "").lower()
            fix = issue.get("fix", "").lower()
            
            # Filter out positive/no-op placeholder warnings generated by the LLM
            if any(term in desc for term in ["no critical", "no errors", "no action needed", "no issues found", "none found"]) or \
               any(term in fix for term in ["no action needed", "none", "n/a"]):
                continue
                
            final_issues.append(issue)
            severity = issue.get("severity", "INFO").upper()
            category = issue.get("category", "")
            
            # Downgrade qualitative LLM issues to INFO to prevent false positives
            cat_lower = category.lower()
            if "re-hook density" in cat_lower or "vocabulary repetition" in cat_lower or "cta clarity" in cat_lower:
                severity = "INFO"
                issue["severity"] = "INFO"
            
            if severity == "CRITICAL":
                pts = 15
            elif severity == "WARNING":
                pts = 5
            elif severity == "INFO":
                pts = 2
            else:
                pts = 0
                
            deductions.append({
                "severity": severity,
                "category": category,
                "description": issue.get("description", ""),
                "points": -pts,
                "reason": f"{severity}: {category}"
            })
            base_score -= pts
            
        calculated_score = max(0, min(100, base_score))
        
        # Override values in review_data
        review_data["issues"] = final_issues
        review_data["quality_score"] = calculated_score
        
        # Recommend upload only if score >= QUALITY_SCORE_THRESHOLD and no CRITICAL issues
        has_critical = any(i.get("severity", "").upper() == "CRITICAL" for i in final_issues)
        review_data["upload_recommended"] = (calculated_score >= QUALITY_SCORE_THRESHOLD) and not has_critical
        
        review_data["scoring_audit"] = {
            "base_score": 100,
            "deductions": deductions,
            "calculated_score": calculated_score
        }
    except Exception as e:
        print(f"[Reviewer] Gemini review failed: {e}")
        return {}

    # Save review
    review_path = os.path.join(LOG_DIR, f"{run_id}_review.json")
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(review_data, f, indent=2)

    return review_data


def print_review(review_data: dict):
    if not review_data:
        print("No review data.")
        return

    score = review_data.get("quality_score", 0)
    upload = review_data.get("upload_recommended", False)
    summary = review_data.get("summary", "")
    issues = review_data.get("issues", [])
    audit = review_data.get("scoring_audit", {})

    print(f"\n{'='*60}")
    print(f"  QUALITY SCORE: {score}/100   |   Upload: {'YES' if upload else 'NO'}")
    if audit:
        base = audit.get("base_score", 100)
        deductions = audit.get("deductions", [])
        deduct_str = " ".join([f"{d.get('points')} ({d.get('reason')})" for d in deductions])
        print(f"  Audit: {base} {deduct_str} = {score}")
    print(f"{'='*60}")
    print(f"  {summary}")
    print(f"{'='*60}\n")

    for issue in issues:
        sev = issue.get("severity", "INFO")
        cat = issue.get("category", "")
        desc = issue.get("description", "")
        fix = issue.get("fix", "")
        icon = {"CRITICAL": "[!!]", "WARNING": "[!]", "INFO": "[i]"}.get(sev, "[?]")
        print(f"  {icon} [{cat}] {desc}")
        if fix:
            print(f"       Fix: {fix}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Auto-find the most recent run
        logs = glob.glob(os.path.join(LOG_DIR, "*_summary.json"))
        if not logs:
            print("Usage: python execution/review_video.py <run_id>")
            sys.exit(1)
        latest = max(logs, key=os.path.getmtime)
        run_id = os.path.basename(latest).replace("_summary.json", "")
        print(f"[Reviewer] Auto-selected most recent run: {run_id}")
    else:
        run_id = sys.argv[1]

    print(f"[Reviewer] Reviewing run: {run_id}")
    result = review(run_id)
    print_review(result)

    if result:
        review_path = os.path.join(LOG_DIR, f"{run_id}_review.json")
        print(f"[Reviewer] Full review saved: {review_path}")
