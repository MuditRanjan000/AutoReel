# Changelog

All notable changes to AutoReel are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-08-16

### Initial Official Public Release

#### Core Architecture & Engines
- **3-Layer Architecture**: Cleanly separated natural language directives (`directives/`), scheduling & orchestration (`scheduler.py`, `execution/`), and deterministic tooling (`core/`).
- **Multi-Channel Subprocess Isolation**: Channel pipelines run in dedicated, isolated child processes preventing cross-contamination and memory leaks.
- **10-Stage Pipeline**: Fully automated workflow covering topic discovery, research, viral scripting, visual retrieval, voice synthesis, video assembly, subtitle animation, quality gating, publishing, and analytics learning.

#### Media & Video Rendering
- **30.0 FPS Video Engine**: Enforced strict 30.0 fps timing, color grading, and aspect ratio normalization (1080x1920) across all FFmpeg filtergraphs.
- **Dynamic Animated Subtitles**: Word-by-word karaoke subtitle generator creating Advanced SubStation Alpha (`.ass`) files with customizable glow, outline, and positioning.
- **Dynamic Audio Ducking**: Automatic BGM ducking during voiceover narration with smooth fade-in/fade-out transitions.

#### AI & Voice Synthesis
- **Multi-Provider LLM Engine**: Key rotation pool with automated fallback cascades across Groq (Llama 3.3 70B), Google Gemini 2.0, OpenRouter, and NVIDIA NIM.
- **Multi-Tier Neural TTS**: Automatic routing between ElevenLabs, Google Cloud TTS (Journey/Neural2), and Microsoft Edge TTS.
- **Phonetic Pronunciation Engine**: SSML dictionary lookup converting complex proper nouns and foreign words into clean spoken phonetics without subtitle bleed.

#### Quality Control & Governance
- **Dual-Layer Quality Gate**: Programmatic verification (duration bounds, repetition density, source factuality) combined with an LLM creative review gate (7.0/10 threshold).
- **SSML Sanitization**: Strips XML tags before text frequency analysis to eliminate false-positive vocabulary warnings.

#### Algorithmic Optimization & Feedback
- **Closed-Loop Analytics**: Daily YouTube Analytics API sync ingesting APV, VVR, and views into SQLite WAL database.
- **Automated Parameter Auto-Tuning**: Algorithmic identification of high-performing hooks, pacing styles, voices, and background music moods with direct config injection.

#### Operator Tooling & Security
- **Telegram Command Center**: Real-time status reporting, pipeline alerts, and remote execution commands.
- **Automated Housekeeping**: Twice-daily storage janitor purging temporary footage and maintaining stable disk utilization.
- **Zero-Secrets Guarantee**: Full sanitization of all private credentials, channel IDs, tokens, and server configurations.
