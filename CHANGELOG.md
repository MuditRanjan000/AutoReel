# Changelog

All notable changes to AutoReel are documented here.

---

## [Unreleased]

### Security
- Added `config/gcp-credentials.json` and `config/youtube_client_secrets.json` to `.gitignore` (were not previously covered)
- Added `SECURITY.md` with credential management guidelines
- Removed tracked runtime/secret files from git index via `git rm --cached`
- Created `.env.example` with placeholder values and inline documentation

### Added
- `LICENSE` (MIT)
- `CONTRIBUTING.md` with architecture context for contributors
- `SECURITY.md` with vulnerability reporting and credential management guidelines
- `.env.example` — template for all required environment variables

### Changed
- **README.md** — complete rewrite with accurate architecture, current tech stack (Google Cloud TTS + ElevenLabs, not edge-tts), step-by-step setup guide, deployment instructions, and Telegram command reference
- **`requirements.txt`** — removed stale `edge-tts` (replaced in V32), de-duplicated `google-cloud-texttospeech`, added version pins and explanatory comments for optional packages
- **`scheduler.py`** — corrected Telegram timeout alert message ("45 minutes" → "75 minutes" to match actual `PIPELINE_TIMEOUT_SECONDS`)
- **`scheduler.py`** — moved `yt-dlp` auto-update from per-pipeline-run to daily storage janitor (saves 5–10s per video render)
- **`core/youtube_uploader.py`** — reduced pre-upload human-pattern delay from 8–22 min to 2–8 min (reduces idle time by up to 56 min/day across 4 channels while preserving anti-bot detection)
- **Bare `except:`** → `except Exception:` across all files: `core/video_assembler.py`, `core/image_scraper.py`, `core/agents/video_quality_reviewer.py`, `core/agents/research_agent.py`, `core/telegram_bot.py`, `config/settings.py`, `execution/review_video.py`, `execution/generate_weekly_report.py`, `execution/generate_daily_report.py`

### Removed
- `temp_clipper.py` — development scratch copy of `core/video_clipper.py`
- `generate_stoic_tests.py`, `generate_test_voices.py`, `test_all_males.py`, `test_keys.py`, `test_voiceover.py` — ad-hoc test scripts
- `run_all_channels.py`, `run_batch_final.py`, `run_remaining_channels.py`, `run_experiments.py` — superseded by `scheduler.py`
- `execution/compliance_upload_test.py`, `execution/test_agency_improvements.py`, `execution/validate_telemetry.py`, `execution/trigger.py` — one-time scripts
- `pipeline_log.txt`, `query_merge_audit.txt`, `antigravity.cmd`, `launch_claude.ps1`, `shutdown_runner.bat` — workspace artifacts
- 25 internal AI assistant / planning markdown documents from repository root

---

## Previous Milestones (V1–V44)

### V44 (Current)
- Stable 24/7 production system across 4 channels
- Quality Engine V1: Gemini visual review gate before upload

### V40
- Multi-channel CEO Scheduler architecture
- Subprocess isolation model for parallel channels

### V32
- Integrated Google Cloud TTS (Studio-quality Journey/Neural2 voices)
- Replaced edge-tts

### V23
- Voiceover caching (MD5 hash of script — skip regeneration if unchanged)

### V1
- Single-channel MVP: RSS → Script → edge-tts → FFmpeg → YouTube

---

> For full commit history, see `git log --oneline`.
