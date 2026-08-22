# ⚙️ AutoReel Configuration & Channel Customization Guide

> AutoReel uses a modular configuration system where each YouTube Shorts channel is defined in a standalone JSON file under `channels/`.

---

## 1. Channel JSON Schema Reference

Create a file named `channels/<your_channel_name>.json`. For example: `channels/curiositydaily.json`.

```json
{
  "CHANNEL_NAME": "curiositydaily",
  "DISPLAY_NAME": "Curiosity Daily",
  "NICHE": "Artificial intelligence, breakthrough tech, robotics, and emerging science",
  "CHANNEL_TONE": "Fast-paced, authoritative, mind-blowing, and highly technical yet accessible",
  "TARGET_AUDIENCE": "Tech enthusiasts, software engineers, AI builders, and future thinkers",
  
  "YOUTUBE_CHANNEL_ID": "UC_YOUR_CHANNEL_ID",
  "YOUTUBE_CATEGORY_ID": "28",
  
  "VOICE_PROVIDER": "edge",
  "voice": "en-US-ChristopherNeural",
  "voice_rate": "+10%",
  "voice_pitch": "+0Hz",
  
  "video_duration_seconds": 55,
  "MIN_WORDS": 110,
  "MAX_WORDS": 140,
  "FPS": 30,
  
  "VISUAL_MODE": "stock_plus_wiki",
  "STOCK_PROVIDER": "pexels",
  "DEFAULT_BGM_MOOD": "sci-fi",
  "BGM_VOLUME": 0.12,
  
  "CAPTION_STYLE": "yellow_glow",
  "FONT_NAME": "Impact",
  "FONT_SIZE": 22,
  
  "CTA_STYLE": "comment_trigger",
  "MAX_VIDEOS_PER_DAY": 2,
  "active": true
}
```

### Parameter Explanations

| Field | Type | Description | Allowed / Recommended Values |
| :--- | :--- | :--- | :--- |
| `CHANNEL_NAME` | string | Unique channel identifier (lowercase, no spaces) | e.g. `curiositydaily`, `truecrime`, `stoicmind` |
| `DISPLAY_NAME` | string | Public YouTube channel name | e.g. `Curiosity Daily` |
| `NICHE` | string | Core topic domain for Trend Scout RSS retrieval | Detailed natural language description |
| `CHANNEL_TONE` | string | Persona and stylistic voice for the AI scriptwriter | e.g. `dramatic`, `contrarian`, `gripping` |
| `VOICE_PROVIDER`| string | Text-to-Speech backend | `"edge"`, `"google"`, `"elevenlabs"` |
| `voice` | string | Voice model identifier | e.g. `en-US-GuyNeural`, `en-US-Journey-D`, or ElevenLabs Voice ID |
| `voice_rate` | string | Speech rate adjustment | `"+5%"`, `"+10%"`, `"+15%"` |
| `video_duration_seconds` | int | Target total video duration | `45` to `58` seconds (Shorts limit is 60s) |
| `VISUAL_MODE` | string | Visual asset retrieval strategy | `"stock_plus_wiki"`, `"pexels_only"`, `"pixabay_only"` |
| `DEFAULT_BGM_MOOD` | string | Default background music mood | `"ambient"`, `"cinematic"`, `"dark"`, `"sci-fi"`, `"trap"`, `"phonk"` |
| `BGM_VOLUME` | float | Background music volume multiplier | `0.08` to `0.15` (speech is normalized to -14 LUFS) |
| `CAPTION_STYLE`| string | Subtitle preset style | `"yellow_glow"`, `"classic_bold"`, `"neon_cyan"`, `"red_alert"` |
| `active` | bool | Whether the CEO scheduler should run this channel | `true` or `false` |

---

## 2. Voice Providers & Configuration

### Option A: Microsoft Edge TTS (Free, Zero Setup)
- Set `"VOICE_PROVIDER": "edge"`
- Popular English voices:
  - `en-US-ChristopherNeural` (Authoritative, clear male)
  - `en-US-GuyNeural` (Energetic, conversational male)
  - `en-US-JennyNeural` (Natural, articulate female)
  - `en-GB-RyanNeural` (Sophisticated British male)

### Option B: Google Cloud TTS (High Quality Neural2 & Journey)
1. Save your service account JSON to `config/gcp-credentials.json`.
2. Set `"VOICE_PROVIDER": "google"`.
3. Popular voice names: `en-US-Journey-D`, `en-US-Journey-F`, `en-US-Neural2-J`.

### Option C: ElevenLabs (Studio Quality Clone / Premium Voices)
1. Add `ELEVENLABS_API_KEY_1` to `.env`.
2. Set `"VOICE_PROVIDER": "elevenlabs"`.
3. Set `"voice"` to your ElevenLabs Voice ID (e.g. `JBFqnCBsd6RMkjVDRZzb`).

---

## 3. Phonetic Pronunciation Customization

AutoReel automatically corrects difficult names and foreign words before speech synthesis. Add custom pronunciations to `config/phonetic_map.json`:

```json
{
  "DeepSeek": "Deep Seek",
  "arXiv": "archive",
  "Nvidia": "En-vidia",
  "Anthropic": "An-thropic"
}
```
AutoReel wraps these in SSML `<sub alias="...">` tags, ensuring perfect pronunciation across all TTS engines without distorting subtitle text.
