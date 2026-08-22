"""
redeploy.py — One-command deployment of latest code to the server.

Usage:
  python scripts/redeploy.py

First run: you will be asked for a GitHub PAT (stored in .deploy_config locally).
"""
import os
import sys
import json
import time
import subprocess
import paramiko
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
HOST = "YOUR_SERVER_IP"
USER = "root"
PASS = None  

AUTOREEL_LOCAL  = str(Path(__file__).resolve().parents[1])
AUTOREEL_REMOTE = "/home/your_user/autoReel"

GH_USER         = "YourGitHubUsername"
AUTOREEL_REPO   = "AutoReel"

CONFIG_FILE     = Path(AUTOREEL_LOCAL) / ".deploy_config"
# ─────────────────────────────────────────────────────────────────────────────

def get_pat():
    pat = os.environ.get("GITHUB_PAT")
    if pat:
        return pat
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())
        if cfg.get("github_pat"):
            return cfg["github_pat"]

    print("\nGitHub Personal Access Token needed (for server to git pull private repos).")
    print("Create one at: https://github.com/settings/tokens")
    print("Required scope: repo (read-only is enough)\n")
    pat = input("Paste your GitHub PAT: ").strip()
    cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    cfg["github_pat"] = pat
    CONFIG_FILE.write_text(json.dumps(cfg))
    print(f"  Saved to {CONFIG_FILE} (gitignored).")
    return pat


def get_ssh_password():
    pwd = os.environ.get("DEPLOY_SSH_PASS")
    if pwd:
        return pwd
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())
        if cfg.get("ssh_password"):
            return cfg["ssh_password"]

    print("\nSSH password needed for deployment server.")
    print("Set DEPLOY_SSH_PASS env var, or enter below to save in .deploy_config (gitignored).")
    pwd = input("Paste SSH password: ").strip()
    cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    cfg["ssh_password"] = pwd
    CONFIG_FILE.write_text(json.dumps(cfg))
    print(f"  Saved to {CONFIG_FILE} (gitignored).")
    return pwd


def connect():
    print(f"\n  Connecting to {HOST}...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=get_ssh_password(), timeout=30)
    print("  Connected.")
    return c


def run(client, cmd, desc="", timeout=120):
    if desc:
        print(f"  {desc}...", flush=True)
    transport = client.get_transport()
    chan = transport.open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    out_lines = []
    while True:
        if chan.recv_ready():
            chunk = chan.recv(4096).decode('utf-8', errors='replace')
            for line in chunk.splitlines():
                print(f"    {line}")
                out_lines.append(line)
        if chan.exit_status_ready():
            break
        time.sleep(0.3)
    return '\n'.join(out_lines)


def local_git_push(local_dir, label):
    print(f"\n  Pushing {label} to GitHub...")
    original_dir = os.getcwd()
    os.chdir(local_dir)
    try:
        subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if result.stdout.strip():
            subprocess.run(
                ["git", "commit", "-m", f"Auto-deploy: {time.strftime('%Y-%m-%d %H:%M')}"],
                check=True, capture_output=True
            )
            print(f"    Committed changes.")
        else:
            print(f"    No local changes to commit.")
        subprocess.run(["git", "push"], check=True, capture_output=True)
        print(f"    Pushed to GitHub.")
    except subprocess.CalledProcessError as e:
        print(f"    WARN: git push failed: {e.stderr.decode()[:200]}")
    finally:
        os.chdir(original_dir)


def ensure_git_repo(client, remote_dir, repo_name, pat):
    check = run(client, f"test -d {remote_dir}/.git && echo YES || echo NO")
    is_git = "YES" in check
    clone_url = f"https://{GH_USER}:{pat}@github.com/{GH_USER}/{repo_name}.git"

    if not is_git:
        print(f"    Setting up git in {remote_dir}...")
        run(client, f"cp {remote_dir}/.env /tmp/{repo_name}_env.bak 2>/dev/null || true")
        run(client, f"git clone {clone_url} /tmp/{repo_name}_clone", timeout=120)
        run(client, f"cp -r /tmp/{repo_name}_clone/. {remote_dir}/")
        run(client, f"rm -rf /tmp/{repo_name}_clone")
        run(client, f"cp /tmp/{repo_name}_env.bak {remote_dir}/.env 2>/dev/null || true")
        run(client, f"cd {remote_dir} && git remote set-url origin {clone_url}")
        print(f"    Git repo initialised.")
    else:
        run(client, f"cd {remote_dir} && git remote set-url origin {clone_url}")
        run(client, f"cd {remote_dir} && git fetch --all && git reset --hard origin/main && git pull", desc=f"git pull {repo_name} (forced)", timeout=120)

    run(client, f"cd {remote_dir} && git rev-parse --short HEAD > .githash")


def redeploy_autoreel(client, pat):
    print("\n── autoReel ──────────────────────────────────────────")
    local_git_push(AUTOREEL_LOCAL, "autoReel")
    ensure_git_repo(client, AUTOREEL_REMOTE, AUTOREEL_REPO, pat)
    run(client, f"cd {AUTOREEL_REMOTE} && ./venv/bin/pip install -r requirements.txt -q",
        desc="pip install (autoReel)", timeout=120)
    run(client, "systemctl restart autoreel", desc="Restarting autoReel service")
    time.sleep(2)
    run(client, "systemctl status autoreel --no-pager")
    print("  autoReel updated and restarted.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)

    pat = get_pat()
    print("\n" + "=" * 50)
    print("  Redeploy: AutoReel")
    print("=" * 50)

    client = connect()
    try:
        redeploy_autoreel(client, pat)
    finally:
        client.close()

    print("\n" + "=" * 50)
    print("  Redeploy complete.")
    print("=" * 50)


if __name__ == "__main__":
    main()
