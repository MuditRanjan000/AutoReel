# 🚀 AutoReel Production Deployment & Server Hardening Guide

> This guide provides step-by-step instructions for deploying AutoReel on a headless Linux cloud server (Ubuntu 22.04 / 24.04 LTS) for 24/7 autonomous operation.

---

## 1. System Requirements & Prerequisites

- **Host OS**: Ubuntu 22.04 or 24.04 LTS
- **Compute**: Minimum 2 vCPUs, 4GB RAM (8GB+ recommended for multi-channel video rendering)
- **Disk**: 20GB+ SSD storage
- **Core Packages**:
```bash
sudo apt-get update && sudo apt-get install -y \
    python3-pip \
    python3-venv \
    ffmpeg \
    imagemagick \
    fonts-liberation \
    fonts-dejavu \
    git \
    sqlite3
```

---

## 2. Clone & Virtual Environment Setup

```bash
# Clone the repository
git clone https://github.com/MuditRanjan000/AutoReel.git
cd AutoReel

# Create and activate Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. Environment & Channel Configuration

```bash
# Copy and configure environment variables
cp .env.example .env
nano .env

# Configure your channels
cp channels/example_channel.json channels/my_channel.json
nano channels/my_channel.json
```

---

## 4. YouTube OAuth2 Channel Authorization

Authorize your YouTube account once via the headless OAuth CLI helper:

```bash
ACTIVE_CHANNEL=my_channel python execution/authorize_youtube.py
```
Follow the console prompt to visit the Google consent URL, grant permissions, and paste the authorization code. A secure token will be saved to `channels/my_channel_token.json`.

---

## 5. Systemd Process Supervisor Setup

To ensure AutoReel automatically restarts on reboots or unexpected errors, configure a `systemd` service:

Create `/etc/systemd/system/autoreel.service`:

```ini
[Unit]
Description=AutoReel Multi-Channel YouTube Automation Scheduler Daemon
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/AutoReel
ExecStart=/home/ubuntu/AutoReel/venv/bin/python scheduler.py
Restart=always
RestartSec=10
StandardOutput=append:/home/ubuntu/AutoReel/output/logs/scheduler.log
StandardError=append:/home/ubuntu/AutoReel/output/logs/scheduler.log
Environment=PYTHONUNBUFFERED=1
Environment=LANG=C.UTF-8
Environment=LC_ALL=C.UTF-8

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable autoreel.service
sudo systemctl start autoreel.service

# Check service status
sudo systemctl status autoreel.service

# Follow live logs
tail -f output/logs/scheduler.log
```

---

## 6. Storage Maintenance & Housekeeping

AutoReel includes an automated twice-daily storage janitor in `scheduler.py` (running at `03:00` and `15:00 IST`) that automatically purges temporary video clips, audio segments, and intermediate renders older than 12 hours.

To run a manual cleanup at any time:
```bash
python execution/cleanup_all.py
```
