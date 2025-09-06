# Repository Guidelines

## Project Structure & Module Organization
- `watcher.py` — core Python pipeline (poll → download → transcribe → insights).
- `podcast-insights` — shell wrapper; manages `.venv`, installs deps, forwards CLI args.
- `config.example.yaml` — template; copy to `config.yaml` (gitignored) and edit paths/feeds.
- `requirements.txt` — Python dependencies.
- `data/`, `logs/`, `state.db` — runtime artifacts (all gitignored).

Directory layout for outputs:
- `data/<podcast_name>/<YYYY-MM-DD>_<episode_title>/`
  - `<episode_title>.mp3`, `.transcript.md`, `.insights.md`

## Build, Test, and Development Commands
- Bootstrap env + show status: `./podcast-insights status`
- One-shot poll (newest episode per feed): `./podcast-insights poll-once`
- Long-running poll loop: `./podcast-insights run`
- First-time config: `cp config.example.yaml config.yaml` and edit tool paths.

## Coding Style & Naming Conventions
- Python 3.10+; 4-space indentation; add type hints where practical.
- Follow existing structure; keep functions small, log via `logging` (no prints).
- Reuse provided safe naming and directory layout; avoid introducing new patterns.
- Shell wrapper should remain POSIX/Bash-safe, `set -euo pipefail`.

## Testing Guidelines
- No formal test suite. Validate locally with `max_new_per_feed: 1` and 1–2 feeds.
- Use `poll-once` for quick iteration; verify files in `data/` and statuses via `status`.
- If adding tests, prefer `pytest` and minimal dependencies; don’t commit fixtures under `data/`.

## Commit & Pull Request Guidelines
- Commit messages: imperative mood, concise summary + context (e.g., “Sort entries newest-first in poll”).
- Do not commit `config.yaml`, `data/`, `logs/`, `.venv/`, or `state.db`.
- PRs should include: purpose, scope of changes, manual test notes (commands run), and any screenshots of resulting directory layout if relevant.

## Security & Configuration Tips
- Keep real paths in `config.yaml` (gitignored); share only `config.example.yaml`.
- Do not hardcode API keys; insights tool reads keys from its own `.env` outside this repo.
- For scheduling, see README launchd example; use absolute paths appropriate to your machine.
