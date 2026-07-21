# 🎬 AutoReel — Your AI YouTube Channel on Autopilot

> AutoReel watches the internet for trending stories, writes a viral script, adds a professional AI voiceover, assembles a YouTube Short — and uploads it — **completely on its own, 24/7, while you sleep.**

No filming. No editing. No microphone. No video skills needed.

---

## 🤔 What Does It Actually Do?

Think of AutoReel as a robot employee that runs your YouTube Shorts channel for you.

Every few hours it:

1. 🔍 **Finds a trending story** on the internet (in whatever topic you choose — true crime, tech, history, finance, etc.)
2. ✍️ **Writes a short viral script** using AI (like ChatGPT but faster and free)
3. 🎙️ **Records a voiceover** using a realistic AI voice — no microphone needed
4. 🎞️ **Finds matching video clips** from free stock footage sites
5. 🎵 **Adds background music** that matches the mood of the video
6. 🎬 **Assembles the final video** with captions burned in
7. 🖼️ **Creates a thumbnail** using AI
8. ✅ **Double-checks the video** for quality before uploading
9. 📤 **Uploads to YouTube** automatically
10. 📱 **Sends you a Telegram message** when it's done (or if something goes wrong)

You can run **multiple channels** at the same time (e.g., one for true crime, one for tech news).

---

## 💰 What Does It Cost?

**You can run AutoReel for completely free.** Here's the breakdown:

| What | Cost | Notes |
|---|---|---|
| AI for writing scripts | **Free** | Groq gives 14,400 free AI requests/day |
| AI voice (narration) | **Free** | Microsoft Edge TTS — no account needed |
| Stock video footage | **Free** | Pexels & Pixabay free API |
| Thumbnail generation | **Free** | Uses Pollinations.ai |
| Subtitles | **Free** | Uses Whisper (runs on your PC) |
| YouTube uploading | **Free** | Uses your own YouTube account |
| Telegram notifications | **Free** | Create a free bot in 2 minutes |

**Optional paid upgrades** (not required):
- 🎙️ **ElevenLabs** — premium AI voices (~$5/mo)
- 🔊 **Google Cloud TTS** — studio-quality voices (free 1M chars/month with credit card on file)

---

## 🖥️ What Computer Do I Need?

AutoReel runs on:
- ✅ **Windows 10/11** (your normal laptop or desktop)
- ✅ **Mac**
- ✅ **Linux / Ubuntu** (for servers)
- ✅ **A cheap cloud server** (DigitalOcean $6/mo droplet — runs 24/7 even when your PC is off)

Minimum specs: **4GB RAM, 10GB free disk space, internet connection**

---

## 🚀 Setup Guide (Step by Step)

Don't worry — each step below has clear instructions. You do not need to be a programmer.

---

### Step 1 — Install Python

Python is the programming language AutoReel is written in. You only need to install it once.

**Windows:**
1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Click the big yellow **"Download Python"** button
3. Run the installer — **✅ make sure to check "Add Python to PATH"** before clicking Install
4. Click Install Now

**Mac:**
1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download and run the installer

**Verify it worked** — open a terminal (search "Command Prompt" on Windows, "Terminal" on Mac) and type:
```
python --version
```
You should see something like `Python 3.11.4`. If you do, Python is ready! ✅

---

### Step 2 — Install FFmpeg

FFmpeg is the video-editing engine AutoReel uses under the hood. It's free.

**Windows (easiest method):**
1. Go to [ffmpeg.org/download.html](https://ffmpeg.org/download.html)
2. Click **Windows** → click the **"gyan.dev"** link → download `ffmpeg-release-essentials.zip`
3. Extract the ZIP file to `C:\ffmpeg`
4. Add it to PATH:
   - Search "Edit the system environment variables" in the Start menu
   - Click "Environment Variables"
   - Under "System variables", find **Path** and click **Edit**
   - Click **New** and type: `C:\ffmpeg\bin`
   - Click OK → OK → OK

**Mac:**
```
brew install ffmpeg
```
(If you don't have Homebrew, install it first from [brew.sh](https://brew.sh))

**Verify it worked:**
```
ffmpeg -version
```
You should see version info. ✅

---

### Step 3 — Download AutoReel

Open your terminal and run:
```
git clone https://github.com/MuditRanjan000/AutoReel.git
cd AutoReel
```

> **Don't have Git?** Download it at [git-scm.com](https://git-scm.com/downloads) and try again.

---

### Step 4 — Install AutoReel's Dependencies

Inside the AutoReel folder, run:
```
pip install -r requirements.txt
```

Then install PyTorch (required for subtitles):
```
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

⏳ This may take a few minutes — it's downloading several AI libraries. Just let it run.

---

### Step 5 — Get Your Free API Keys

AutoReel needs a few free accounts to work. Think of API keys as passwords that let AutoReel access these services on your behalf.

#### 5a. Groq API Key (for writing scripts — FREE)

1. Go to [console.groq.com](https://console.groq.com) and create a free account
2. Click **"API Keys"** in the left menu → **"Create API Key"**
3. Copy the key (it starts with `gsk_...`)

#### 5b. Pexels API Key (for video footage — FREE)

1. Go to [pexels.com/api](https://www.pexels.com/api/) and sign up free
2. After signing in, your API key is shown on the API page — copy it

#### 5c. Telegram Bot (to get notified on your phone — FREE)

1. Open Telegram on your phone
2. Search for **@BotFather** and tap Start
3. Send `/newbot` — give it any name (e.g. "My AutoReel Bot")
4. BotFather will give you a **Bot Token** — copy it (looks like `1234567890:AAF...`)
5. Now search for **@userinfobot** in Telegram, tap Start — it will show your **Chat ID** (a number like `987654321`) — copy that too

---

### Step 6 — Create Your Config File (.env)

This file stores all your API keys. AutoReel reads it automatically.

1. In the AutoReel folder, find the file called `.env.example`
2. **Make a copy of it** and rename the copy to `.env` (no ".example")
   - Windows: right-click → Copy → Paste → rename to `.env`
3. Open `.env` with Notepad (right-click → Open with → Notepad)
4. Fill in your keys:

```
GROQ_API_KEY_1=gsk_paste_your_groq_key_here

PEXELS_API_KEY_1=paste_your_pexels_key_here

TELEGRAM_BOT_TOKEN=paste_your_bot_token_here
TELEGRAM_CHAT_ID=paste_your_chat_id_here
```

5. Save the file

> ⚠️ **Never share this file with anyone.** It contains your private keys.

---

### Step 7 — Set Up Your YouTube Channel

AutoReel needs permission to upload videos to your YouTube channel.

1. Go to [Google Cloud Console](https://console.cloud.google.com) and sign in with the Google account that owns your YouTube channel
2. Click **"Select a project"** at the top → **"New Project"** → give it any name → click Create
3. In the left menu, go to **APIs & Services → Library**
4. Search for **"YouTube Data API v3"** → click it → click **Enable**
5. Go to **APIs & Services → Credentials** → click **"Create Credentials"** → choose **"OAuth client ID"**
6. Application type: **Desktop app** → click Create
7. Click **Download JSON** → save this file as `config/youtube_client_secrets.json` inside your AutoReel folder

Now run this once to authorize AutoReel to use your account:
```
python execution/authorize_youtube.py
```
A browser window will open — log in with your YouTube account and click Allow. Done! ✅

---

### Step 8 — Create Your Channel Config

Each YouTube channel you want AutoReel to manage needs its own settings file.

1. In the AutoReel folder, open the `channels` folder
2. Make a copy of `example_channel.json` and rename it to match your channel (e.g. `mychannel.json`)
3. Open it with Notepad and edit these fields:

```json
{
  "CHANNEL_NAME": "Your Channel Name",
  "NICHE": "describe what your channel is about — e.g. true crime stories",
  "CHANNEL_TONE": "how should it sound — e.g. dramatic and suspenseful",
  "YOUTUBE_CHANNEL_ID": "paste your YouTube channel ID here",
  "voice": "en-US-GuyNeural",
  "voice_rate": "+10%",
  "MAX_VIDEOS_PER_DAY": 2,
  "active": true
}
```

> **How to find your YouTube Channel ID:**
> Go to [youtube.com](https://youtube.com) → click your profile → **YouTube Studio** → **Settings** → **Channel** → **Advanced settings** — your Channel ID is the `UC...` string at the top.

---

### Step 9 — Pick Your AI Voice (FREE options)

AutoReel uses Microsoft's free AI voices by default. No account or credit card needed.

Just set the `"voice"` field in your channel config to one of these:

| Voice Name | How It Sounds |
|---|---|
| `en-US-GuyNeural` | Deep American male (good for news/crime) |
| `en-US-JennyNeural` | Warm American female |
| `en-US-AriaNeural` | Friendly American female |
| `en-GB-RyanNeural` | British male |
| `en-GB-SoniaNeural` | British female |
| `en-AU-WilliamNeural` | Australian male |
| `en-IN-NeerjaNeural` | Indian female |
| `en-IN-PrabhatNeural` | Indian male |

To hear all available voices, run:
```
python -c "import asyncio, edge_tts; asyncio.run(edge_tts.list_voices())"
```

---

### Step 10 — Run a Test Video

Before going 24/7, let's make sure everything works by generating one video for your new channel:

```
ACTIVE_CHANNEL=mychannel python execution/run_pipeline.py
```

AutoReel will:
- Find a trending story
- Write a script
- Generate a voiceover
- Download footage
- Assemble the video
- **Ask you on Telegram whether to upload** (if Telegram is configured)

Check the `output/` folder — you should see a `.mp4` file! ✅

If something goes wrong, check `output/logs/pipeline.log` — it describes every step.

---

### Step 11 — Start AutoReel 24/7

Once your test video works, start the full scheduler:

```
python scheduler.py
```

AutoReel will now run by itself, posting videos at the times you've set. You'll get a Telegram message every time a video goes up.

Press `Ctrl + C` to stop it.

---

## 📱 Telegram Control Commands

Once AutoReel is running, you can control it from your phone using these Telegram commands:

| Type this | What happens |
|---|---|
| `/status` | Shows how many videos were posted today and system health |
| `/force mychannel` | Make it post a video right now (replace `mychannel` with your channel name) |
| `/skip mychannel` | Skip the next scheduled post |
| `/pause` | Pause everything |
| `/resume` | Resume everything |
| `/channels` | See all your active channels |
| `/cookies` | Update YouTube cookies (paste Netscape file) |
| `/check_cookies` | Verify current YouTube cookies status |
| `/help` | See all available commands |
---

## 🔊 Voice Options — Which One Should I Use?

| Voice System | Cost | Quality | Setup Required |
|---|---|---|---|
| **Microsoft Edge TTS** ⭐ | Free | Great | None — works immediately |
| **Google Cloud TTS** | Free (1M chars/month with card) | Studio quality | Need GCP account + credit card |
| **ElevenLabs** | From $5/month | Best, most human | Need to sign up at elevenlabs.io |

**Recommendation for beginners:** Start with Edge TTS (the default). It sounds great and requires zero setup.

---

## ❓ Common Problems & Fixes

**"python is not recognized"**
→ Python wasn't added to PATH during installation. Re-install Python and make sure to check ✅ "Add Python to PATH"

**"ffmpeg is not recognized"**
→ FFmpeg wasn't added to PATH. Re-do Step 2.

**"No valid Groq API keys found"**
→ Your `.env` file doesn't have the key, or the key has a typo. Open `.env` in Notepad and check.

**"FileNotFoundError: channels/mychannel.json"**
→ The channel name in your command doesn't match the filename. If your file is `mychannel.json`, use `ACTIVE_CHANNEL=mychannel`.

**Video was generated but not uploaded**
→ Make sure `AUTO_POST_YOUTUBE=True` is in your `.env` and you ran `authorize_youtube.py`

**I got an error I don't understand**
→ Open `output/logs/pipeline.log` in Notepad and look at the last few lines — they describe exactly what failed.

---

## 🌐 Running 24/7 Without Your PC (Optional)

If you want AutoReel to keep running even when your computer is off, you can host it on a cheap cloud server.

**DigitalOcean** offers a $6/month server (called a "droplet") that runs 24/7.

1. Sign up at [digitalocean.com](https://www.digitalocean.com)
2. Create a droplet: **Ubuntu 22.04, Basic, $6/mo**
3. SSH into it and follow the same setup steps above
4. To keep it running after you log out:

```bash
nohup python scheduler.py &> output/logs/scheduler.log &
```

Or set it up as a proper background service — see the [Deployment section](docs/deployment.md) for detailed instructions.

---

## 📁 What's in the AutoReel Folder?

Here's a plain-English map of the important files:

```
AutoReel/
├── scheduler.py          ← The brain — runs everything on a schedule
├── channels/             ← Your channel settings (one file per channel)
├── config/
│   ├── settings.py       ← Global settings
│   └── gcp-credentials.json  ← (Optional) Google Cloud credentials
├── execution/
│   ├── run_pipeline.py   ← Run this to make one video manually
│   └── authorize_youtube.py  ← Run this once to connect YouTube
├── output/               ← Generated videos go here
│   └── logs/             ← Log files — check here when something goes wrong
├── .env                  ← Your private API keys (never share this!)
└── requirements.txt      ← List of libraries AutoReel needs
```

---

## 🤖 Tech Stack (for the curious)

| Part | What it uses |
|---|---|
| AI Script Writing | Groq (Llama 3) → Gemini → NVIDIA → OpenRouter |
| Free Voiceover | Microsoft Edge TTS |
| Premium Voiceover | Google Cloud TTS, ElevenLabs |
| Subtitles | OpenAI Whisper |
| Video Assembly | FFmpeg |
| Stock Footage | Pexels, Pixabay, YouTube (yt-dlp) |
| Thumbnails | Pollinations.ai |
| YouTube Upload | YouTube Data API v3 |
| Notifications | Telegram Bot API |

---

## 📄 License

MIT — free to use, modify, and share. See [LICENSE](LICENSE) for details.

## 🔐 Security

See [SECURITY.md](SECURITY.md) for how to keep your API keys safe.
