# Directive: Manage Gemini API Quota

## Context
The pipeline uses Gemini for two steps:
1. **Trend Fetcher** (`core/trend_fetcher.py`) — picks the most viral story from RSS feeds
2. **Script Generator** (`core/script_generator.py`) — writes the full voiceover script

The free tier of Gemini Flash allows **20 requests/day** per API key (resets midnight Pacific Time).
Each full pipeline run uses **2 Gemini calls** (1 per step above).
So the free tier supports ~10 runs/day maximum.

## Current Fallback Behavior
- **Trend Fetcher**: If quota is hit, automatically falls back to a local keyword scorer (no Gemini call needed). The story picked may be slightly less optimal but the pipeline continues.
- **Script Generator**: If quota is hit, raises a clear `RuntimeError` with instructions. The pipeline stops here — there is no fallback for script generation (it genuinely needs LLM creativity).
- **Story Cache**: The chosen story is cached for 6 hours in `output/logs/story_cache.json`. Repeated runs within 6 hours reuse the same story without burning a Gemini call.

## Solutions (in order of ease)

### Option 1: Wait for Quota Reset
Quota resets at midnight Pacific Time daily.
- 12:00 AM PT = 1:30 PM IST
- If it's already past that, try again now.

### Option 2: Add a Second API Key
1. Go to https://aistudio.google.com and sign in with a different Google account
2. Create a new API key
3. Add to `.env`:
   ```
   GEMINI_API_KEY_2=your_new_key_here
   ```
4. Update `config/settings.py` to use it as fallback:
   ```python
   import os
   GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "your_primary_key")
   ```
5. Update `core/script_generator.py` and `core/trend_fetcher.py` to try `GEMINI_API_KEY_2` on 429.

### Option 3: Use Gemini API Key Rotation (Advanced)
Run `execution/rotate_api_keys.py` — tries each key in sequence and picks the first one with quota remaining.
(Script needs to be created — see `execution/` directory.)

## Monitoring Quota
Check the Google AI Studio dashboard: https://aistudio.google.com/apikey
Each key shows its usage and remaining quota.

## Learnings
- `gemini-3-flash` and `gemini-flash-latest` share the same quota pool — they're the same model.
- The retry delay in the 429 error (`retry in Xs`) is for RPM (requests per minute), not RPD (requests per day). Waiting that long won't fix a daily quota exhaustion.
- The trend fetcher was burning quota by being called multiple times per pipeline run attempt. Fixed by adding the 6-hour story cache.
