# Contributing to AutoReel

Thank you for your interest in contributing! This is a personal automation project that is open to community improvements.

## Getting Started

1. **Fork** the repository and clone your fork
2. **Set up** your environment following the `README.md` Quick Start
3. **Create a branch** for your feature or fix: `git checkout -b feat/your-feature-name`
4. **Make your changes** — keep commits small and focused
5. **Test** your changes against at least one channel config
6. **Open a Pull Request** against the `main` branch

## Code Style

- Python 3.10+ features are fine
- Use `except Exception:` not bare `except:`
- All secrets go in `.env` — never hardcoded
- Keep module-level imports; avoid runtime imports inside hot paths
- Docstrings on all public classes and methods

## Before Opening a PR

- [ ] No secrets or credentials committed
- [ ] `requirements.txt` updated if you added dependencies
- [ ] Code works with the subprocess-isolation model (settings loaded fresh per process)
- [ ] `CHANGELOG.md` updated with a one-line description of your change

## Architecture Notes

- **`scheduler.py`** — CEO Agent. Orchestrates all channels via `subprocess.run`. Do not add blocking work here.
- **`execution/run_pipeline.py`** — Canonical pipeline. Every step has structured error handling (`ContentFailure` vs `InfrastructureFailure`).
- **`core/channel_context.py`** — The `ChannelContext` object is the single source of truth for per-channel config. Pass it; don't read `os.environ` directly.
- **`core/gemini_client.py`** — All LLM calls go through `generate_with_rotation`. Do not call AI APIs directly.
- **`core/db.py`** — SQLite in WAL mode. Use `get_connection()` and always call `conn.close()`.

## Reporting Issues

Open a GitHub issue with:
- What you expected to happen
- What actually happened
- Relevant log output from `output/logs/pipeline.log`
- Your OS and Python version
