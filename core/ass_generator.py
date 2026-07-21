"""
core/ass_generator.py
Generates viral-style .ass subtitle files from Whisper word timestamps.

Caption strategy:
  - Groups words into NATURAL PHRASES (2-4 words) based on:
      1. Whisper pause detection: gap > 0.18s = new card (natural breath break)
      2. Sentence punctuation: period/comma/question mark = new card
      3. Max 4 words per card — never feels rushed
  - Resolves premium modern system fonts (Arial Black / Trebuchet MS)
  - Active Word Karaoke Highlights: only the spoken word is lit up in color, keeping eyes locked on screen
  - Punch pop-in animation on each card
  - Positioned lower-center, above the safe zone
"""

import re
import os
import subprocess
import difflib
import math
import time as _time
import json
import whisper
from config.settings import VOCABULARY

# ── Whisper Model Singleton ───────────────────────────────────────────────────
# Load once at module level, reuse across all pipeline runs.
# Allocating 500MB per call (previous behavior) wasted 10-30s per video.
_whisper_model = None

def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        print("[ASSGen] Loading Whisper 'small' model (one-time init)...")
        _whisper_model = whisper.load_model("small")
    return _whisper_model


def clean_dotted_acronyms(text: str) -> str:
    if not text:
        return text
    # USA / VIP
    text = re.sub(r'\b[Uu]\.\s*[Ss]\.\s*[Aa]\.(?=\s*[A-Z]|\s*$)', 'USA.', text)
    text = re.sub(r'\b[Uu]\.\s*[Ss]\.\s*[Aa]\.', 'USA', text)
    text = re.sub(r'\b[Vv]\.\s*[Ii]\.\s*[Pp]\.(?=\s*[A-Z]|\s*$)', 'VIP.', text)
    text = re.sub(r'\b[Vv]\.\s*[Ii]\.\s*[Pp]\.', 'VIP', text)
    # US / AI / DC / IT / BC / AD / PM / AM / PS
    acronyms = [
        ('U', 'S', 'US'),
        ('A', 'I', 'AI'),
        ('D', 'C', 'DC'),
        ('I', 'T', 'IT'),
        ('B', 'C', 'BC'),
        ('A', 'D', 'AD'),
        ('P', 'M', 'PM'),
        ('A', 'M', 'AM'),
        ('P', 'S', 'PS'),
    ]
    for char1, char2, replacement in acronyms:
        pattern_sentence = rf'\b[{char1}{char1.lower()}]\.\s*[{char2}{char2.lower()}]\.(?=\s*[A-Z]|\s*$)'
        pattern_general = rf'\b[{char1}{char1.lower()}]\.\s*[{char2}{char2.lower()}]\.?'
        text = re.sub(pattern_sentence, replacement + '.', text)
        text = re.sub(pattern_general, replacement, text)
    return text


def is_word_in_script(word: str, script_words: set) -> bool:
    cleaned = re.sub(r'[^A-Z0-9]', '', word.upper())
    if not cleaned:
        return True
    
    # 1. Exact match
    if cleaned in script_words:
        return True
        
    # 2. Substring match (either cleaned word is inside a script word, or vice versa)
    # Only do this for words longer than 2 characters to avoid too-broad matches with short words (like "IN", "TO", "BY")
    if len(cleaned) > 2:
        for sw in script_words:
            if cleaned in sw or sw in cleaned:
                return True
                
    # 3. Fuzzy similarity match (e.g. minor transcription spelling variations like "Common" vs "Comment")
    if len(cleaned) >= 4:
        for sw in script_words:
            if len(sw) >= 4:
                # If length difference is small and similarity is high
                if abs(len(cleaned) - len(sw)) <= 2:
                    sim = difflib.SequenceMatcher(None, cleaned, sw).ratio()
                    if sim >= 0.7:
                        return True

    # 4. Handle common number word translations if digits exist in script_words
    number_words = {"ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN",
                    "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN",
                    "EIGHTEEN", "NINETEEN", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY",
                    "EIGHTY", "NINETY", "HUNDRED", "THOUSAND", "MILLION", "BILLION"}
    if cleaned in number_words:
        # Check if there is any digit in any word of script_words
        if any(any(c.isdigit() for c in sw) for sw in script_words):
            return True
            
    # 5. Contraction fragments/splits like "DON" and "T" from "DON'T"
    if len(cleaned) == 1 and cleaned in "TDS":
        return True
    return False


def match_casing_and_punctuation(original: str, replacement: str) -> str:
    """Preserves leading/trailing non-alphanumeric punctuation and aligns replacement case to original."""
    leading_punct = re.match(r'^[^A-Za-z0-9]+', original)
    trailing_punct = re.search(r'[^A-Za-z0-9]+$', original)
    
    lead = leading_punct.group(0) if leading_punct else ""
    trail = trailing_punct.group(0) if trailing_punct else ""
    
    # Strip leading/trailing punctuation to assess casing
    stripped_orig = re.sub(r'^[^A-Za-z0-9]+|[^A-Za-z0-9]+$', '', original)
    
    if not stripped_orig:
        return replacement
        
    if stripped_orig.isupper():
        rep = replacement.upper()
    elif stripped_orig.istitle() or (len(stripped_orig) > 0 and stripped_orig[0].isupper()):
        rep = replacement.title()
    elif stripped_orig.islower():
        rep = replacement.lower()
    else:
        rep = replacement
        
    return f"{lead}{rep}{trail}"


COMMON_ENGLISH_WORDS = {
    "the", "of", "to", "and", "a", "in", "is", "it", "you", "that", "he", "was", "for", "on", "are", 
    "as", "with", "his", "they", "i", "at", "be", "this", "have", "from", "or", "one", "had", "by", 
    "word", "but", "not", "what", "all", "were", "we", "when", "your", "can", "said", "there", "use", 
    "an", "each", "which", "she", "do", "how", "their", "if", "will", "up", "other", "about", "out", 
    "many", "then", "them", "these", "so", "some", "her", "would", "make", "like", "him", "into", 
    "time", "has", "look", "two", "more", "write", "go", "see", "number", "no", "way", "could", 
    "people", "my", "than", "first", "water", "been", "call", "who", "oil", "its", "now", "find", 
    "long", "down", "day", "did", "get", "come", "made", "may", "part", "words", "world", "hard",
    "back", "only", "then", "than", "that", "them", "they", "their", "here", "there", "where", "when",
    "why", "how", "who", "what", "which", "whom", "this", "these", "those", "each", "both", "some",
    "any", "same", "other", "another", "such", "own", "very", "so", "too", "also", "just", "quite"
}


def correct_word_spelling(word: str, vocab_words: set) -> str:
    """Corrects word spelling if it has a high fuzzy match ratio to any term in vocabulary."""
    cleaned = re.sub(r'[^A-Z0-9]', '', word.upper())
    if not cleaned:
        return word
        
    # Sort vocab words so longer/exact matches are checked first (to prioritize e.g. "Hardik" over similar shorter words)
    sorted_vocab = sorted(list(vocab_words), key=len, reverse=True)
    
    # 1. Exact match (case insensitive check)
    for vw in sorted_vocab:
        cleaned_vw = re.sub(r'[^A-Z0-9]', '', vw.upper())
        if cleaned == cleaned_vw:
            return match_casing_and_punctuation(word, vw)
            
    # 2. Skip fuzzy matching if input word is a common English word
    if cleaned.lower() in COMMON_ENGLISH_WORDS:
        return word
        
    # 3. Fuzzy match (only for words of length >= 4) with threshold 0.75
    for vw in sorted_vocab:
        cleaned_vw = re.sub(r'[^A-Z0-9]', '', vw.upper())
        if not cleaned_vw:
            continue
        
        if len(cleaned) >= 4 and len(cleaned_vw) >= 4:
            if abs(len(cleaned) - len(cleaned_vw)) <= 2:
                sim = difflib.SequenceMatcher(None, cleaned, cleaned_vw).ratio()
                if sim >= 0.75:
                    return match_casing_and_punctuation(word, vw)
    return word


def apply_sequence_corrections(raw_words: list, script_words: set = None, vocabulary: list = None) -> list:
    """
    Applies sequence-based spelling corrections to fix Whisper phonetic transcript errors
    for specific multi-word proper names (e.g. "Hardik Pandya", "Tilak Varma") by merging
    mis-transcribed tokens while preserving timestamps and punctuation.
    """
    if not raw_words:
        return raw_words

    # Dictionary mapping tuples of uppercase stripped words to tuple of (replacement_word, replacement_raw)
    corrections = {
        ("HEART", "ACHE"): [("HARDIK", "Hardik")],
        ("HEART", "ACHES"): [("HARDIK", "Hardik")],
        ("HARD", "ACHE"): [("HARDIK", "Hardik")],
        ("HARD", "ACHES"): [("HARDIK", "Hardik")],
        ("HEARTACHE",): [("HARDIK", "Hardik")],
        ("HEARTACHES",): [("HARDIK", "Hardik")],
        ("HARDACHE",): [("HARDIK", "Hardik")],
        ("HARDACHES",): [("HARDIK", "Hardik")],
        ("HARD", "DICK"): [("HARDIK", "Hardik")],
        ("HARD", "DECKS"): [("HARDIK", "Hardik")],
        ("HARD", "DECK"): [("HARDIK", "Hardik")],
        ("TEA", "LOCK"): [("TILAK", "Tilak")],
        ("TEA", "LOOK"): [("TILAK", "Tilak")],
        ("TEA", "LUCK"): [("TILAK", "Tilak")],
        ("TILL", "LOCK"): [("TILAK", "Tilak")],
        ("TILL", "LOOK"): [("TILAK", "Tilak")],
        ("TILL", "LUCK"): [("TILAK", "Tilak")],
        ("TIE", "LOCK"): [("TILAK", "Tilak")],
        ("TIE", "LOOK"): [("TILAK", "Tilak")],
        ("TEA", "LOG"): [("TILAK", "Tilak")],
        ("TEELUK",): [("TILAK", "Tilak")],
        ("TEELUCK",): [("TILAK", "Tilak")],
        ("TILLOCK",): [("TILAK", "Tilak")],
        ("TILACK",): [("TILAK", "Tilak")],
        ("TILAC",): [("TILAK", "Tilak")],
        ("TEE", "LAK"): [("TILAK", "Tilak")],
        ("TEA", "LAK"): [("TILAK", "Tilak")],
        ("TEE-LAK",): [("TILAK", "Tilak")],
        ("TEA-LAK",): [("TILAK", "Tilak")],
        ("TEELAK",): [("TILAK", "Tilak")],
        ("VERMA",): [("VARMA", "Varma")],
        ("PANDEY",): [("PANDYA", "Pandya")],
        ("PANDY",): [("PANDYA", "Pandya")],
        ("PANDIA",): [("PANDYA", "Pandya")],
        ("PANDE",): [("PANDYA", "Pandya")],
        # Cricket terminology mishears (British accent Whisper errors)
        ("CATCHER",): [("CATCH", "catch")],
        ("CATCHERS",): [("CATCHES", "catches")],
        ("DROPPED", "CATCHER"): [("DROPPED", "dropped"), ("CATCH", "catch")],
        ("WICKETS", "KEEPER"): [("WICKETKEEPER", "wicketkeeper")],
        ("OVER", "THROUGH"): [("OVERTHROW", "overthrow")],
        ("RAN", "OUT"): [("RUNOUT", "runout")],
        ("POWER", "PLAY"): [("POWERPLAY", "powerplay")],
        ("DEATH", "OVER"): [("DEATH OVER", "death over")],
    }

    # Build a set of vocabulary words in uppercase for quick lookup
    vocab_to_use = vocabulary if vocabulary is not None else VOCABULARY
    vocab_upper = {v.upper() for v in vocab_to_use}
    if (script_words and "MI" in script_words) or "MI" in vocab_upper:
        corrections[("HIM", "EITHER")] = [("MI", "MI"), ("THE", "the")]
        corrections[("THEM", "EITHER")] = [("MI", "MI"), ("THE", "the")]

    corrected = []
    i = 0
    n = len(raw_words)
    while i < n:
        matched = False
        # Try matching sequence lengths from 3 down to 1
        for seq_len in [3, 2, 1]:
            if i + seq_len <= n:
                # Strip common punctuation to match raw Whisper tokens
                seq_tokens = tuple(w["word"].upper().strip(",.?!:;()\"'-") for w in raw_words[i:i+seq_len])
                if seq_tokens in corrections:
                    replacements = corrections[seq_tokens]
                    start_time = raw_words[i]["start"]
                    end_time = raw_words[i+seq_len-1]["end"]
                    
                    num_rep = len(replacements)
                    duration = end_time - start_time
                    step = duration / num_rep if num_rep > 0 else 0
                    
                    for r_idx, (rep_word, rep_raw) in enumerate(replacements):
                        # Extract punctuation prefix from the first word
                        prefix = ""
                        orig_start_raw = raw_words[i]["raw"]
                        leading_match = re.match(r'^[^A-Za-z0-9]+', orig_start_raw)
                        if leading_match:
                            prefix = leading_match.group(0)
                        
                        # Extract punctuation suffix from the last word
                        suffix = ""
                        orig_end_raw = raw_words[i+seq_len-1]["raw"]
                        trailing_match = re.search(r'[^A-Za-z0-9]+$', orig_end_raw)
                        if trailing_match:
                            suffix = trailing_match.group(0)
                            
                        final_raw = f"{prefix}{rep_raw}{suffix}"
                        
                        corrected.append({
                            "word": f"{prefix.upper()}{rep_word}{suffix.upper()}",
                            "start": start_time + r_idx * step,
                            "end": start_time + (r_idx + 1) * step,
                            "raw": final_raw
                        })
                    
                    i += seq_len
                    matched = True
                    break
        if not matched:
            corrected.append(raw_words[i])
            i += 1
            
    return corrected


def script_align_words(raw_words: list, script: str, audio_duration: float = None) -> list:
    """
    Forced Script Alignment — the ultimate fix for Whisper caption errors.

    Whisper's word-level TIMESTAMPS are usually accurate, but its TEXT is often wrong:
      - "KUDHAVS" instead of "COULD-HAVES"
      - "DENNO" instead of "NO"
      - "CAN AM I BOUNCED" instead of "CAN MI BOUNCE BACK"

    This function uses Whisper ONLY for timing and replaces all caption text with the
    original correct script words, eliminating transcription errors from captions entirely.

    Strategy (difflib SequenceMatcher alignment):
      - equal   : script word matches Whisper word → use script text + Whisper timing
      - replace : Whisper said wrong word(s) → redistribute Whisper timing to script words
      - insert  : Whisper hallucinated extra words → discard entirely
      - delete  : Whisper skipped script words → interpolate timing from neighbors
    """
    if not script or not raw_words:
        return raw_words

    # Strip any SSML tags so they aren't forced into the captions
    script = re.sub(r'<[^>]+>', '', script)
    script = clean_dotted_acronyms(script)

    # Tokenize the original script into individual words, preserving punctuation attachment
    # e.g. "what-ifs," stays as one token, "could-haves." stays as one token
    script_raw_tokens = re.findall(r"[\w'\-]+[.,!?;:]*", script)
    # Filter out pure punctuation / empty tokens
    script_raw_tokens = [t for t in script_raw_tokens if re.search(r'[A-Za-z0-9]', t)]

    if not script_raw_tokens:
        return raw_words

    # Normalized versions for sequence comparison (strip punctuation, uppercase)
    def norm(s):
        return re.sub(r'[^A-Z0-9]', '', s.upper())

    s_norms = [norm(t) for t in script_raw_tokens]
    w_norms = [norm(w['word']) for w in raw_words]

    # Drop empty normalized tokens (e.g. pure punctuation)
    s_valid = [(i, n, script_raw_tokens[i]) for i, n in enumerate(s_norms) if n]
    w_valid = [(i, n) for i, n in enumerate(w_norms) if n]

    if not s_valid or not w_valid:
        return raw_words

    s_idx  = [x[0] for x in s_valid]
    s_norm_list = [x[1] for x in s_valid]
    s_raw_list  = [x[2] for x in s_valid]
    w_idx  = [x[0] for x in w_valid]
    w_norm_list = [x[1] for x in w_valid]

    matcher = difflib.SequenceMatcher(None, s_norm_list, w_norm_list, autojunk=False)
    aligned = []

    for tag, s_i, s_j, w_i, w_j in matcher.get_opcodes():
        s_count = s_j - s_i
        w_count = w_j - w_i

        if tag == 'equal':
            # Perfect match — use script text, Whisper timing
            for k in range(s_count):
                ww = raw_words[w_idx[w_i + k]]
                aligned.append({
                    'word': s_raw_list[s_i + k].upper(),
                    'raw':  s_raw_list[s_i + k],
                    'start': ww['start'],
                    'end':   ww['end'],
                })

        elif tag == 'replace':
            # Whisper got timing roughly right but wrong text — spread timing over script words
            t_start = raw_words[w_idx[w_i]]['start']
            t_end   = raw_words[w_idx[w_j - 1]]['end']
            if s_count > 0:
                step = (t_end - t_start) / s_count
                for k in range(s_count):
                    aligned.append({
                        'word': s_raw_list[s_i + k].upper(),
                        'raw':  s_raw_list[s_i + k],
                        'start': t_start + k * step,
                        'end':   t_start + (k + 1) * step,
                    })

        elif tag == 'insert':
            # Whisper hallucinated words not in script — discard
            print(f"[ASSGen:Align] Discarding {w_count} Whisper hallucination(s): "
                  f"{[raw_words[w_idx[w_i+k]]['raw'] for k in range(w_count)]}")

        elif tag == 'delete':
            # Script words Whisper didn't transcribe — interpolate timing
            prev_end = aligned[-1]['end'] if aligned else 0.0
            if w_i < len(w_idx):
                next_start = raw_words[w_idx[w_i]]['start']
            elif audio_duration:
                next_start = audio_duration
            else:
                next_start = prev_end + 0.25 * s_count
            step = (next_start - prev_end) / (s_count + 1) if s_count > 0 else 0.25
            for k in range(s_count):
                t_s = prev_end + (k + 1) * step
                t_e = min(prev_end + (k + 2) * step, next_start)
                aligned.append({
                    'word': s_raw_list[s_i + k].upper(),
                    'raw':  s_raw_list[s_i + k],
                    'start': t_s,
                    'end':   t_e,
                })
                print(f"[ASSGen:Align] Interpolated timing for skipped word: '{s_raw_list[s_i + k]}'")

    print(f"[ASSGen] Forced alignment: {len(s_raw_list)} script words -> "
          f"{len(aligned)} aligned caption words (from {len(raw_words)} Whisper words)")
    return aligned


def generate_ass(audio_path: str, output_path: str, recipe: dict = None, script = None, ctx = None):
    """
    Transcribe with Whisper (base model) for word-level TIMESTAMPS only.
    Then force-align original script words to those timestamps so captions
    always display the correct script text — never Whisper's mishears.
    Groups aligned words into natural phrase cards and writes .ass file with karaoke highlights.
    If script is a dict containing 'scenes', it also writes a sidecar _scenes.json for dynamic assembly.
    """
    model  = _get_whisper_model()

    script_dict = None
    if isinstance(script, dict):
        script_dict = script
        script_text = script.get("full_script", "")
    else:
        script_text = script

    # Normalize script words to allow robust filtering of end-of-audio hallucinations
    script_words = set()
    if script_text:
        # Strip all XML/SSML tags to prevent hallucination vocabulary injection
        script_text_no_xml = re.sub(r'<[^>]+>', '', script_text)
        script_clean_text = clean_dotted_acronyms(script_text_no_xml)
        # Tokenize by finding word sequences (including apostrophes)
        raw_tokens = re.findall(r"[A-Z0-9']+", script_clean_text.upper())
        for t in raw_tokens:
            cleaned = re.sub(r'[^A-Z0-9]', '', t)
            if cleaned:
                script_words.add(cleaned)
    
    recipe = recipe or {}
    
    vocab_to_use = ctx.vocabulary if (ctx is not None and hasattr(ctx, 'vocabulary')) else VOCABULARY

    # Build vocabulary set (splitting multi-word phrases to catch single words as well)
    vocab_words = set()
    for item in vocab_to_use:
        vocab_words.add(item)
        for part in item.split():
            clean_part = re.sub(r'[^A-Za-z0-9]', '', part)
            if len(clean_part) >= 3:
                vocab_words.add(clean_part)
                
    pri_color = recipe.get("caption_color_primary", "Yellow")
    sec_color = recipe.get("caption_color_secondary", "White")
    niche = recipe.get("topic", "artificial intelligence, future tech news, wealth building, finance, cricket analysis")
    
    # Merge global niche vocabulary with the provided topic
    full_vocab = list(set(vocab_to_use + [niche]))
    initial_prompt = f"Terminology: {', '.join(full_vocab)}. Proper grammar, professional vocabulary, correct names, percentages like 13.6%."
    # --- Whisper Transcription with retry logic for Windows file flushing / transient errors ---
    script_token_count = len(re.findall(r"[A-Za-z0-9']+", script_text)) if script_text else 0
    max_transcribe_attempts = 3
    result = None
    
    # Let the filesystem flush and settle before opening the file
    _time.sleep(0.5)

    for attempt in range(1, max_transcribe_attempts + 1):
        try:
            if attempt > 1:
                print(f"[ASSGen] Too few Whisper words found. Attempting file settle sleep (1.5s) and retry {attempt}/{max_transcribe_attempts}...")
                _time.sleep(1.5)
            
            result = model.transcribe(
                audio_path,
                word_timestamps=True,
                fp16=False,
                initial_prompt=initial_prompt,
                temperature=0,                   # Deterministic — no random creative hallucinations
                condition_on_previous_text=False, # Prevents cascading hallucination at audio tail
                no_speech_threshold=0.6,         # Drop segments where Whisper isn't sure speech exists
                compression_ratio_threshold=2.4, # Drop highly repetitive / garbled outputs
                logprob_threshold=-1.0,          # Drop very low confidence words
            )
            
            # Count words transcribed
            temp_word_count = 0
            for seg in result.get("segments", []):
                if seg.get("avg_logprob", 0) >= -0.8 and seg.get("no_speech_prob", 0) < 0.6:
                    temp_word_count += len(seg.get("words", []))
            
            # If we have a script and transcribed words are less than 60% of the script words, retry.
            if script_text and script_token_count > 10 and temp_word_count < (script_token_count * 0.6):
                print(f"[ASSGen] Warning: Whisper transcribed only {temp_word_count} words for a {script_token_count}-word script.")
                if attempt < max_transcribe_attempts:
                    continue
            
            # Succeeded or exhausted retries
            break
        except Exception as ex:
            print(f"[ASSGen] Transcribing attempt {attempt} failed: {ex}")
            if attempt >= max_transcribe_attempts:
                raise
            _time.sleep(1.5)

    # --- Anti-Hallucination: Get real audio duration and filter low-confidence segments ---
    try:
        dur_cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                   "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
        audio_duration = float(subprocess.check_output(dur_cmd).decode().strip())
    except Exception:
        audio_duration = None

    # Filter out hallucinated segments: avg_logprob < -0.8 = garbage text
    filtered_segments = []
    for seg in result.get("segments", []):
        if seg.get("avg_logprob", 0) >= -0.8 and seg.get("no_speech_prob", 0) < 0.6:
            filtered_segments.append(seg)
    result["segments"] = filtered_segments

    COLOR_MAP = {
        "Yellow": "00FFFF",
        "White":  "FFFFFF",
        "Green":  "00FF00",
        "Red":    "0000FF",
        "Cyan":   "FFFF00"
    }
    
    def _parse_color(c, default="00FFFF"):
        if isinstance(c, list) and len(c) == 3:
            r, g, b = int(c[0]), int(c[1]), int(c[2])
            return f"{b:02X}{g:02X}{r:02X}"
        if isinstance(c, str):
            if c.startswith("#"):
                c = c.lstrip("#").upper()
                if len(c) == 6:
                    return c[4:6] + c[2:4] + c[0:2]
            return COLOR_MAP.get(c, default)
        return default

    pri_ass = f"&H00{_parse_color(pri_color, '00FFFF')}&"
    sec_ass = f"&H00{_parse_color(sec_color, 'FFFFFF')}&"

    _channel_name = ctx.channel_name.lower() if (ctx is not None and hasattr(ctx, 'channel_name')) else ""

    # Dynamic premium font selection based on OS installations & Channel DNA
    if "crime" in _channel_name:
        selected_font = "Courier New"
        align_str = "5" # Center
        border_style = "1" # Normal outline
        outline = "5"
        shadow = "3"
        bg_col = "&H00000000&" # Transparent background
        font_size = "85"
        italic = "0"
    elif "stoic" in _channel_name:
        selected_font = "Times New Roman"
        align_str = "5" # Center
        border_style = "1"
        outline = "3"
        shadow = "5"
        bg_col = "&H00000000&"
        font_size = "90"
        italic = "0"
    elif "cricket" in _channel_name:
        selected_font = "Impact"
        align_str = "5"
        border_style = "1"
        outline = "4"
        shadow = "0"
        bg_col = "&H00000000&"
        font_size = "100"
        italic = "0" # Non-italicized
    else: # Culture or default
        selected_font = "Impact" # default safe fallback
        if os.path.exists("C:/Windows/Fonts/ariblk.ttf"):
            selected_font = "Arial Black"
        elif os.path.exists("C:/Windows/Fonts/trebucbd.ttf"):
            selected_font = "Trebuchet MS"
        align_str = "5"
        border_style = "1"
        outline = "10"
        shadow = "4"
        bg_col = "&H00000000&"
        font_size = "95"
        italic = "0"

    # Safe-zone positioning
    _caption_y_override = getattr(ctx, 'caption_y', None) if ctx else None
    if _caption_y_override is not None:
        SUBTITLE_Y = int(_caption_y_override)
    elif "culture" in _channel_name or "surge" in _channel_name.replace("example_channel_3", ""):
        SUBTITLE_Y = 1550  # Lower — keeps faces visible for culture/women-focused channels
    else:
        SUBTITLE_Y = 1100  # Default — lower-center safe zone

    header = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{selected_font},{font_size},&H00FFFFFF&,&H000000FF&,&H00000000&,{bg_col},-1,{italic},0,0,100,100,2,0,{border_style},{outline},{shadow},{align_str},80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    PAUSE_THRESHOLD = 0.18   # seconds — gap > this = natural card break
    if "culture" in _channel_name or "cricket" in _channel_name:
        MAX_WORDS = 3
    elif "crime" in _channel_name:
        MAX_WORDS = 5
    elif "stoic" in _channel_name:
        MAX_WORDS = 4
    else:
        MAX_WORDS = 5
    MIN_WORD_DURATION = 0.02 # minimum seconds a karaoke highlight must stay visible (< this = invisible flash)
    MIN_CARD_DURATION = 0.20 # minimum seconds an entire card must stay on screen
    END_PUNCT       = re.compile(r"[.!?…]$")   # sentence-ending punctuation

    def fmt(s: float) -> str:
        h   = int(s // 3600)
        m   = int((s % 3600) // 60)
        sec = s % 60
        return f"{h}:{m:02d}:{sec:05.2f}"

    # ── Flatten word list ────────────────────────────────────────────────────
    raw_words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            text = w["word"].strip()
            if not text:
                continue
                
            # Run spelling correction against vocabulary
            corrected_text = correct_word_spelling(text, vocab_words)
            if corrected_text != text:
                print(f"[ASSGen] Auto-corrected spelling: '{text}' -> '{corrected_text}'")
                text = corrected_text
            # Hard cap: discard any word that starts beyond the real audio duration
            if audio_duration is not None and w["start"] >= audio_duration:
                continue

            # Shift Whisper timestamps early to compensate for processing/windowing lag
            shift = getattr(ctx, "caption_shift", 0.12) if ctx is not None else 0.12
            start_adjusted = max(0.0, w["start"] - shift)
            end_adjusted = max(0.0, w["end"] - shift)

            raw_words.append({
                "word":  text.upper(),
                "start": start_adjusted,
                "end":   min(end_adjusted, audio_duration) if audio_duration else end_adjusted,
                "raw":   text,
            })

    # Apply sequence corrections before forced alignment
    raw_words = apply_sequence_corrections(raw_words, script_words=script_words, vocabulary=vocab_to_use)

    # ── Forced Script Alignment — replace Whisper text with correct script words —————
    # This is the definitive fix for errors like "KUDHAVS" → "COULD-HAVES",
    # "DENNO" → "NO", "CAN AM I BOUNCED" → "CAN MI BOUNCE BACK".
    # Whisper timing is kept; only the displayed text is replaced with the original script.
    if script_text:
        raw_words = script_align_words(raw_words, script_text, audio_duration)

    # Filter out tail hallucinations (still relevant for any words beyond script length)
    final_raw_words = []
    for w in raw_words:
        # --- Subtitle Hallucination Filtering ---
        # If word starts within the last 3.5s of the audio, and it is not in the script
        # (and script is provided), we discard it to prevent Whisper tail hallucinations (e.g. "(bye)", "Bye")
        if script_text and audio_duration is not None and w["start"] >= (audio_duration - 3.5):
            if not is_word_in_script(w["word"], script_words):
                print(f"[ASSGen] Filtering tail hallucination: '{w['word']}' at {w['start']:.2f}s (Audio Duration: {audio_duration:.2f}s)")
                continue
        final_raw_words.append(w)
    raw_words = final_raw_words

    if not raw_words:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header)
        return

    # ── Merge Decimals, Percentages, and Broken Punctuation ────────────────
    # Prevents large numbers (like 10,000) or split numbers (like 10 000) from breaking across lines
    words = []
    for w in raw_words:
        word_text = w["word"]
        # Merge criteria: 
        # 1. Starts with dot + followed by digit (e.g. .6)
        # 2. Starts with comma + followed by digit (e.g. ,000)
        # 3. Is exactly percent symbol or ends with percent symbol
        # 4. Last word ended with a decimal dot and current is a digit
        # 5. Last word ends with a digit and current consists of only digits
        if words and (
            (word_text.startswith(".") and len(word_text) > 1 and word_text[1].isdigit()) or
            (word_text.startswith(",") and len(word_text) > 1 and word_text[1].isdigit()) or
            word_text == "%" or
            word_text.startswith("%") or
            (word_text.isdigit() and words[-1]["word"].endswith(".")) or
            (word_text.isdigit() and words[-1]["word"][-1].isdigit())
        ):
            words[-1]["word"] += word_text
            words[-1]["end"] = w["end"]
            words[-1]["raw"] += w["raw"]
        else:
            words.append(w)

    # ── Natural Phrase Grouping (Upgraded Card Line-Balancing) ─────────────
    # Groups words up to natural pauses/punctuation, then distributes them evenly.
    phrases = []
    current_phrase = []
    
    for i, w in enumerate(words):
        current_phrase.append(w)
        is_last = (i == len(words) - 1)
        if not is_last:
            next_w = words[i + 1]
            gap = next_w["start"] - w["end"]
            if gap > PAUSE_THRESHOLD or END_PUNCT.search(w["raw"]):
                phrases.append(current_phrase)
                current_phrase = []
        else:
            phrases.append(current_phrase)
            
    # Split long phrases evenly to avoid awkward 1-word cards (e.g., balance 5 words into 3 and 2)
    cards = []
    for phrase in phrases:
        n = len(phrase)
        if n <= MAX_WORDS:
            cards.append(phrase)
        else:
            num_cards = math.ceil(n / MAX_WORDS)
            avg = n / num_cards
            start_idx = 0
            for c in range(num_cards):
                end_idx = int(round((c + 1) * avg))
                card = phrase[start_idx:end_idx]
                if card:
                    cards.append(card)
                start_idx = end_idx

    # ── Dialogue Generation using Separate Lines per Word ───────────────────
    # Generates a separate dialogue line for each word in the card.
    # Keeps text static after the first word pop-in to prevent libass overlaps.
    raw_dialogue_data = []
    for idx, card in enumerate(cards):
        if not card:
            continue
            
        # 1. Seamless Card Boundaries
        card_start = card[0]["start"]
        if idx + 1 < len(cards):
            card_end = cards[idx + 1][0]["start"]
        else:
            card_end = card[-1]["end"] + 0.4

        card_end = max(card_end, card_start + MIN_CARD_DURATION)

        # 2. Seamless Word Timings (Inside Card)
        n_words = len(card)
        for word_idx, active_word in enumerate(card):
            # First word starts exactly at card_start to ensure continuity
            w_start = card_start if word_idx == 0 else active_word["start"]
            
            # Highlight ends exactly when the next word starts (or when the card ends)
            if word_idx == n_words - 1:
                w_end = card_end
            else:
                w_end = card[word_idx + 1]["start"]

            # Safety clamp: Enforce minimum visible duration so karaoke highlight actually renders
            w_end = max(w_end, w_start + MIN_WORD_DURATION)

            # Build the text line with the active word highlighted
            parts = []
            current_y = SUBTITLE_Y

            is_stoic = "stoic" in _channel_name
            
            # Build the text block
            for w_i, w in enumerate(card):
                # Ensure we use standard ASCII apostrophes, as curly quotes break FFmpeg/libass font rendering
                word_text = w["word"].replace("’", "'").replace("‘", "'").replace("´", "'")
                
                scale_tag = ""
                reset_tag = ""
                
                if is_stoic:
                    # Option A: The Ghost Reveal
                    if w_i == word_idx:
                        # Active word fading from dark transparent to opaque glowing primary color
                        parts.append(f"{{\\alphaA0\\t(0,250,\\alpha00)}}{{\\c{pri_ass}}}{scale_tag}{word_text}{reset_tag}{{\\c{sec_ass}}}")
                    elif w_i < word_idx:
                        # Past words: fully opaque primary color (stays lit)
                        parts.append(f"{{\\alpha00}}{{\\c{pri_ass}}}{word_text}{{\\c{sec_ass}}}")
                    else:
                        # Future words: dark grey (highly transparent)
                        parts.append(f"{{\\alphaA0}}{{\\c{sec_ass}}}{word_text}")
                else:
                    # Instant Snap Reveal (Clean Karaoke Highlight)
                    if w_i == word_idx:
                        # Active word highlighted in primary color
                        parts.append(f"{{\\alpha00\\blur0}}{{\\c{pri_ass}}}{scale_tag}{word_text}{reset_tag}{{\\c{sec_ass}}}")
                    elif w_i < word_idx:
                        # Past words in secondary color
                        parts.append(f"{{\\alpha00\\blur0}}{{\\c{sec_ass}}}{word_text}")
                    else:
                        # Future words are invisible but maintain spacing
                        parts.append(f"{{\\alphaFF\\blur0}}{word_text}")
                
                if w_i < len(card) - 1:
                    parts.append(" ")

            text = "".join(parts)
            raw_dialogue_data.append((w_start, w_end, text, current_y))

    # 3. Flawless Monotonic Timing Filter: dynamially adjust boundaries to guarantee 0.0s time overlap
    lines = []
    last_end_time = 0.0
    x_pos = 80 if align_str in ["4", "1", "7"] else 540
    for w_start, w_end, text, current_y in raw_dialogue_data:
        # Enforce that current line starts only when the previous line ends
        if w_start < last_end_time:
            w_start = last_end_time
        # Enforce positive duration with minimum visibility floor
        if w_end <= w_start:
            w_end = w_start + MIN_WORD_DURATION
            
        lines.append(f"Dialogue: 0,{fmt(w_start)},{fmt(w_end)},Cap,,0,0,0,,{{\\pos({x_pos},{current_y})}}{text}")
        last_end_time = w_end

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(lines) + "\n")

    print(f"[ASSGen] Generated {len(lines)} balanced active karaoke caption cards -> {output_path}")

    # ── Scene Timings Export ────────────────────────────────────────────────
    # Write a sidecar _scenes.json with precise [start, end] derived from words
    if script_dict and "scenes" in script_dict:
        scenes = script_dict["scenes"]
        scene_timings = []
        
        # Simple greedy matcher: walk through aligned words
        word_idx = 0
        total_words = len(raw_words)
        for s_idx, scene in enumerate(scenes):
            narr = scene.get("narration", "").strip()
            if not narr:
                continue
            # Tokenize narration into words to know how many words this scene spans
            narr_tokens = re.findall(r"[\w'\-]+", narr)
            narr_tokens_len = len(narr_tokens)
            
            # Find start and end times
            if word_idx < total_words:
                if not scene_timings:
                    scene_start = 0.0  # First scene covers intro silence
                else:
                    scene_start = scene_timings[-1]["end"]  # Contiguous with previous
                
                # Advance word_idx by the length of the scene's narration
                end_idx = min(word_idx + narr_tokens_len - 1, total_words - 1)
                scene_end = raw_words[end_idx]["end"]
                word_idx += narr_tokens_len
            else:
                if not scene_timings:
                    scene_start = 0.0
                else:
                    scene_start = scene_timings[-1]["end"]
                scene_end = scene_start + 1.0  # fallback
            
            # If this is the last scene, stretch to end of audio duration
            if s_idx == len(scenes) - 1 and audio_duration is not None:
                scene_end = audio_duration
                
            scene_timings.append({
                "narration": narr,
                "query": scene.get("query", ""),
                "start": scene_start,
                "end": scene_end
            })
            
        scenes_path = output_path.replace(".ass", "_scenes.json")
        try:
            with open(scenes_path, "w", encoding="utf-8") as f:
                json.dump({"scenes": scene_timings}, f, indent=2)
            print(f"[ASSGen] Extracted precise timings for {len(scene_timings)} scenes -> {scenes_path}")
        except Exception as e:
            print(f"[ASSGen] Error writing scenes JSON: {e}")
