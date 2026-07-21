import subprocess
import os
import sys
from datetime import datetime

from config.settings import LOG_DIR
LOG_FILE = os.path.join(LOG_DIR, "git_backup.log")

def log(msg: str):
    """Log to console and git_backup.log file."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"Failed to write log to file: {e}")

def run_git_cmd(args: list) -> tuple:
    """Run a git command and return (stdout, stderr, returncode)."""
    try:
        res = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=60
        )
        return res.stdout.strip(), res.stderr.strip(), res.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out after 60 seconds", -1
    except Exception as e:
        return "", str(e), -1

def backup():
    log("=== Starting Automated Secure Git Backup ===")
    
    # 1. Check if git is initialized
    if not os.path.exists(".git"):
        log("ERROR: .git directory not found. Please ensure git is initialized in the workspace root.")
        return False

    # 2. Check current branch name
    stdout, stderr, code = run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0 or not stdout:
        # Default to 'master' or 'main' depending on environment if not yet committed
        branch = "main"
        log(f"Warning: Could not detect current branch (no commits yet). Defaulting to '{branch}'.")
    else:
        branch = stdout
        log(f"Detected active branch: '{branch}'")

    # 3. Check if remote origin exists
    stdout, stderr, code = run_git_cmd(["remote", "get-url", "origin"])
    has_remote = (code == 0 and bool(stdout))
    
    if not has_remote:
        log("-----------------------------------------------------------------")
        log("NOTICE: No remote 'origin' configured for this git repository yet.")
        log("To complete automatic pushing, please run these commands:")
        log("  git remote add origin <your-private-github-repo-url>")
        log("  git branch -M main")
        log("  git push -u origin main")
        log("We will still perform local staging and commits, but pushing is skipped.")
        log("-----------------------------------------------------------------")
    else:
        log(f"Configured remote origin: {stdout}")

    # 4. Pull remote changes to prevent divergence if remote exists
    if has_remote:
        log(f"Pulling latest changes from remote '{branch}' (rebase)...")
        # Try to pull, but don't fail hard if remote branch doesn't exist yet (e.g. fresh repo)
        pull_stdout, pull_stderr, pull_code = run_git_cmd(["pull", "origin", branch, "--rebase"])
        if pull_code != 0:
            log(f"Pull warning/info: {pull_stderr or pull_stdout} (might be a new remote branch)")
        else:
            log("Pull completed successfully.")

    # 5. Stage files (.gitignore takes care of excluding secrets & binaries)
    log("Staging modified, added, and deleted files...")
    _, add_stderr, add_code = run_git_cmd(["add", "."])
    if add_code != 0:
        log(f"ERROR adding files to staging: {add_stderr}")
        return False

    # 6. Check if there are any staged changes to commit
    status_stdout, _, status_code = run_git_cmd(["status", "--porcelain"])
    if status_code != 0:
        log("ERROR checking git status.")
        return False
    
    if not status_stdout:
        log("No changes detected. Workspace is clean. Backup up-to-date.")
        log("=== Finished Backup (No changes) ===")
        return True

    # 7. Get staged changes details to build a professional commit body
    diff_stdout, _, diff_code = run_git_cmd(["diff", "--cached", "--name-status"])
    changes_desc = ""
    if diff_code == 0 and diff_stdout:
        status_map = {
            'A': 'Added',
            'M': 'Modified',
            'D': 'Deleted',
            'R': 'Renamed',
            'C': 'Copied',
            'U': 'Unmerged'
        }
        lines = []
        for line in diff_stdout.splitlines():
            parts = line.split('\t')
            if len(parts) >= 2:
                status_char = parts[0][0]
                status_name = status_map.get(status_char, 'Changed')
                filepath = parts[1]
                lines.append(f"- [{status_name}] {filepath}")
        if lines:
            changes_desc = "Staged files:\n" + "\n".join(lines)

    # 8. Commit changes with automatic timestamp and files list
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_title = f"Auto-backup: {timestamp_str}"
    
    commit_args = ["commit", "-m", commit_title]
    if changes_desc:
        commit_args += ["-m", changes_desc]
        log(f"Committing changes with message:\n> {commit_title}\n{changes_desc}")
    else:
        log(f"Committing changes with message: '{commit_title}'")
        
    commit_stdout, commit_stderr, commit_code = run_git_cmd(commit_args)
    if commit_code != 0:
        log(f"ERROR committing changes: {commit_stderr}")
        return False
    log(f"Commit successful: {commit_stdout.splitlines()[0] if commit_stdout else 'Done'}")


    # 8. Push to remote origin
    if has_remote:
        log(f"Pushing commits to remote 'origin {branch}'...")
        push_stdout, push_stderr, push_code = run_git_cmd(["push", "origin", branch])
        if push_code != 0:
            log(f"ERROR pushing to remote origin: {push_stderr}")
            log("Please verify your GitHub credentials / token is authenticated on this machine.")
            return False
        log("Push completed successfully!")
    else:
        log("Staged and committed changes locally. Remote pushing skipped (no remote origin configured).")

    log("=== Git Backup Successfully Completed ===")
    return True

if __name__ == "__main__":
    success = backup()
    sys.exit(0 if success else 1)
