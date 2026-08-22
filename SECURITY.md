# Security Policy

## Supported Versions

AutoReel is a personal automation project under active development. Only the latest version on the `main` branch receives security attention.

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not open a public GitHub issue**.

Instead, email: [contact the repo maintainer privately]

Please include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact

You can expect a response within 48 hours.

## Credential Management

This project requires several API keys and OAuth credentials. Follow these rules:

### ✅ DO

- Copy `.env.example` → `.env` and fill in your own credentials
- Store your Google Cloud service account key (`config/gcp-credentials.json`) locally only
- Keep OAuth token files (`channels/*_token.json`) locally only
- Use `.gitignore` — all sensitive files are already listed

### ❌ DO NOT

- Commit `.env`, `config/gcp-credentials.json`, or any `*_token.json` files to Git
- Share API keys in GitHub issues, pull requests, or comments
- Use the repository maintainer's API keys (they won't work — rotate after any exposure)

### If You Accidentally Commit a Secret

1. **Immediately rotate the exposed credential** — revoke it and generate a new one
2. Remove the file from git tracking: `git rm --cached <file>`
3. Use [BFG Repo Cleaner](https://rtyley.github.io/bfg-repo-cleaner/) or `git filter-repo` to scrub history
4. Force-push the cleaned history

## Required Credentials

| Credential | Source | Sensitivity |
|---|---|---|
| Groq API keys | [console.groq.com](https://console.groq.com) | High |
| Gemini API keys | [aistudio.google.com](https://aistudio.google.com) | High |
| Google Cloud service account JSON | [console.cloud.google.com](https://console.cloud.google.com) | **Critical** (private key) |
| YouTube OAuth2 client secrets | [console.cloud.google.com](https://console.cloud.google.com) | High |
| YouTube OAuth2 tokens | Generated at runtime | **Critical** (account access) |
| ElevenLabs API keys | [elevenlabs.io](https://elevenlabs.io) | High |
| Telegram bot token | [t.me/BotFather](https://t.me/BotFather) | High |
| Pexels / Pixabay API keys | Their respective portals | Medium |
