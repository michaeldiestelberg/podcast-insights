# Podcast Insights

An interactive terminal application for browsing podcast RSS feeds, downloading episodes, transcribing audio to Markdown, and extracting insights — all on your Mac.

![Podcast Insights Library](docs/podcast-insights_library.png)

## Features

- 📻 Browse multiple podcast RSS feeds in a clean terminal interface
- 📥 Download and process episodes on-demand
- 📝 Transcribe audio to Markdown using local ML models
- 🧠 Extract key insights using AI
- 💾 Local storage with organized folder structure
- 🎯 Process any episode from any point in the feed history

## Requirements

- macOS with Python 3.10+ (3.13 recommended)
- ffmpeg installed (for transcription)
- External tools:
  - Transcription: [local-podcast-transcription](https://github.com/michaeldiestelberg/local-podcast-transcription)
  - Insights: [ai-cli](https://github.com/michaeldiestelberg/ai-cli)

## Installation

1. Clone this repository:
```bash
git clone https://github.com/yourusername/podcast-insights.git
cd podcast-insights
```

2. Copy and configure settings:
```bash
cp config.example.yaml config.yaml
```

3. Edit `config.yaml` to add:
   - Your podcast RSS feed URLs
   - Paths to transcription and insights tools
   - Storage locations (default: `./data`)

## Usage

Launch the application:
```bash
./podcast-insights
```

The wrapper script automatically:
- Creates a Python virtual environment
- Installs dependencies
- Launches the interface

### Navigation

**Main Screen:**
- Browse your configured podcasts
- See episode counts (new/processed)
- Type number + Enter to select a podcast

**Episode List:**
- View episodes with processing status
- Type number + Enter to process an episode
- Press `l` to load more episodes
- Press `ESC` to go back
- Press `q` to quit

**Processing:**
- Watch real-time progress through three stages:
  1. 📥 Download audio
  2. 📝 Transcribe audio
  3. 🧠 Extract insights
- Results are saved to the data directory

## Configuration

### config.yaml Structure

```yaml
storage:
  data_dir: ./data        # Where to save episodes
  temp_dir: ./data/_tmp   # Temporary download location

runtime:
  max_retries: 3          # Download retry attempts
  retry_backoff_seconds: 5

tools:
  transcribe_cmd: "/path/to/transcribe.sh \"{audio}\" -o \"{transcript}\""
  insights_cmd: "/path/to/ai-prompt --prompt \"{transcript}\" --system-prompt podcast-insights --output-path \"{episode_dir}\" --output-name \"{insights_file}\""

feeds:
  - url: https://example.com/podcast.rss
    name: Example Podcast
```

### Storage Layout

Episodes are organized by podcast and date:
```
data/
  PodcastName/
    2024-01-15_EpisodeTitle/
      EpisodeTitle.mp3
      EpisodeTitle.transcript.md
      EpisodeTitle.insights.md
```

## First Run

On first use:
- The app fetches all episodes from your configured feeds
- Browse any episode from the full history
- Process episodes on-demand as needed
- The transcription tool may download ML models (one-time ~500MB)

## Troubleshooting

**Missing dependencies:**
- Install ffmpeg: `brew install ffmpeg`

**Config not found:**
- Ensure you've created `config.yaml` from the example

**Tools not working:**
- Verify tool paths in config.yaml
- Test tools directly with their own documentation

**No episodes showing:**
- Check feed URLs are correct
- Ensure internet connection for initial feed fetch

## Development

Built with:
- Python 3.10+ for core logic
- Rich library for terminal UI
- SQLite for episode tracking
- External tools for transcription and insights

## License

MIT