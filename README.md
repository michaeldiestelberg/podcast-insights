# Podcast Insights

A lightweight, local-first pipeline that monitors podcast RSS feeds, downloads new episodes, transcribes audio to Markdown, and extracts insights — all on your Mac. It uses your existing transcription tool and AI CLI for insights.

## Highlights

- Monitor 5–15 RSS feeds locally; newest-first processing
- Sequential pipeline: download → transcribe → insights
- Local storage with clear, per-podcast/per-episode folders
- PEP 668 friendly via a shell wrapper that manages a local venv
- Idempotent steps: safely resumes and skips already-completed work

## Requirements

- macOS with Python 3.10+ (3.13 recommended)
- ffmpeg installed (for your transcription tool)
- Your existing tools installed and working:
  - Transcription tool: clone https://github.com/michaeldiestelberg/local-podcast-transcription and use its `transcribe.sh`
  - Insights tool: clone https://github.com/michaeldiestelberg/ai-cli and use its `ai-prompt` with `.env` API keys and a `podcast-insights` system prompt

For setup and usage of these tools, see their repositories:
- Transcription: https://github.com/michaeldiestelberg/local-podcast-transcription
- Insights: https://github.com/michaeldiestelberg/ai-cli

## Install

1) Clone/open this repo.

2) No global installs required — use the wrapper. It will create a local virtualenv and install dependencies into it:

```bash
./podcast-insights status
```

This bootstraps `.venv/` and installs `requirements.txt` (feedparser, requests, pyyaml).

## Configure

Copy the example config and edit it with your local paths and feeds:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` to set feeds, storage locations, runtime, and tool commands.

```yaml
storage:
  data_dir: ./data
  temp_dir: ./data/_tmp

runtime:
  poll_interval_minutes: 30
  max_retries: 3
  retry_backoff_seconds: 5
  sequential: true
  max_new_per_feed: 1   # Process only the newest episode per feed per run

tools:
  # Adjust these paths to where you cloned the tools
  transcribe_cmd: "/path/to/local-podcast-transcription/transcribe.sh \"{audio}\" -o \"{transcript}\""
  insights_cmd: "/path/to/ai-cli/ai-prompt --prompt \"{transcript}\" --system-prompt podcast-insights --output-path \"{episode_dir}\" --output-name \"{insights_file}\" --model gpt-5-mini"

feeds:
  - url: https://feeds.megaphone.fm/hubermanlab
    name: Huberman Lab
  - url: https://api.substack.com/feed/podcast/10845.rss
    name: How I AI
```

Placeholders used in `tools` commands:
- `{audio}`: Full path to the episode MP3
- `{transcript}`: Full path to transcript Markdown file
- `{episode_dir}`: Directory containing the episode files
- `{insights_file}`: File name for the insights output

## Run

- One-shot (poll feeds and process newest episode per feed):

```bash
./podcast-insights poll-once
```

- Long-running loop (poll every `poll_interval_minutes`):

```bash
./podcast-insights run
```

- Status summary:

```bash
./podcast-insights status
```

First run notes:
- Transcription tool may download its ML model (hundreds of MB) on first use.
- Insights tool uses your API keys; costs may apply depending on the model/provider.

## Storage Layout

Outputs are stored by podcast and episode with safe, human-readable names:

```
./data/
  <podcast_name_safe>/
    <YYYY-MM-DD>_<episode_title_safe>/
      <episode_title_safe>.mp3
      <episode_title_safe>.transcript.md
      <episode_title_safe>.insights.md
```

Safe naming allows letters, numbers, spaces, dashes, underscores; long names are truncated with a short hash suffix to avoid collisions.

## How It Works

- Feed polling
  - Uses ETag/Last-Modified to avoid full downloads when feeds haven’t changed
  - Parses entries and selects the newest-first for each feed
- Episode state (SQLite `state.db`)
  - Tracks `feeds` and `episodes` with status: `new → downloaded → transcribed → done`
  - Idempotent: if an output file already exists, the step is skipped
- Pipeline (sequential)
  - Download MP3 with streaming and atomic move
  - Run transcription to produce `<title>.transcript.md`
  - Run insights to produce `<title>.insights.md`

## Scheduling (Optional, launchd)

You can keep it running automatically with a user LaunchAgent.

1) Create a plist at `~/Library/LaunchAgents/com.podcast.insights.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.podcast.insights</string>

  <key>ProgramArguments</key>
  <array>
    <string>/path/to/podcast-insights/podcast-insights</string>
    <string>run</string>
  </array>

  <key>WorkingDirectory</key>
  <string>/path/to/podcast-insights</string>

  <key>StandardOutPath</key>
  <string>/path/to/podcast-insights/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>/path/to/podcast-insights/logs/launchd.err.log</string>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
```

2) Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.podcast.insights.plist
```

3) Later, unload:

```bash
launchctl unload ~/Library/LaunchAgents/com.podcast.insights.plist
```

Notes:
- Ensure the `ProgramArguments` path matches your repo path.
- The wrapper manages the venv and dependencies automatically.
- Your insights `.env` remains in the `ai-cli` project and is handled by that tool.

## Git Hygiene

- The repository includes `.gitignore` entries for `config.yaml`, `data/`, `logs/`, `.venv/`, and `state.db` so your local paths and artifacts aren’t committed.
- Keep `config.example.yaml` up to date when changing configuration structure so others can bootstrap easily.

## Troubleshooting

- Missing transcript or insights file
  - Check tool paths in `config.yaml`
  - Verify the external tools run standalone with the same arguments
- API keys not found
  - Ensure `ai-cli/.env` contains the required keys and the `podcast-insights` system prompt exists in its prompt library
- ffmpeg not found
  - `brew install ffmpeg`
- Nothing new is downloaded
  - Feeds may return 304 (no new entries). We still process any pending episodes.
  - Increase `max_new_per_feed` to process additional back-catalog items.

## Development Notes

- Code entry point: `watcher.py`
- Wrapper script: `podcast-insights` (creates .venv, installs dependencies, runs CLI)
- Dependencies: see `requirements.txt`
- Local state: `state.db`, logs in `./logs/`

## Roadmap

- Optional `post_done_cmd` hook (e.g., send insights to Notion)
- Optional `ingest-local` command to process existing MP3s without RSS
