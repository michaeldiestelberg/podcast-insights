# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Core Commands

### Initial Setup
```bash
# Automatic setup (recommended)
git clone --recursive https://github.com/michaeldiestelberg/podcast-insights.git
cd podcast-insights
./setup.sh  # Interactive setup wizard

# Manual setup
cp config.example.yaml config.yaml
# Edit config.yaml with tool paths and feeds
```

### Running the Application
```bash
# Launch the application
./podcast-insights

# Update bundled tools
./podcast-insights --update

# With custom config
./podcast-insights --config path/to/config.yaml
```

## Architecture Overview

### Application Structure
Single-mode interactive terminal UI for browsing and processing podcast episodes.

**Core Modules:**
- `podcast_insights.py`: Main application and TUI controller
- `database.py`: Database operations (DB and ExtendedDB classes)
- `models.py`: Configuration and state dataclasses
- `utils.py`: Helper functions and utilities
- `processors.py`: Feed and episode processing logic
- `ui_components.py`: UI rendering components
- `config_manager.py`: Configuration management and auto-detection
- `podcast-insights`: Shell wrapper managing virtualenv and updates
- `setup.sh`: Interactive setup wizard for installation
- `update.sh`: Tool update script for submodules

### Processing Pipeline
User-initiated sequential pipeline: **Select Episode → Choose Mode → Download → Transcribe → Extract Insights**

**Processing Modes:**
- **Full**: Download + Transcribe + Insights (status ends at `done`)
- **Transcribe only**: Download + Transcribe (status ends at `transcribed`, can add insights later)
- **Insights only**: Extract insights from existing transcript (for `transcribed` episodes)

**Bulk Processing:**
- Select multiple episodes: `1,3,5` (comma-separated), `1-5` (range), or `all`
- Sequential processing with compact progress UI
- Warns and confirms when episodes will be skipped

Each step:
- Checks for existing output files (idempotent)
- Updates SQLite database status
- Suppresses subprocess output in UI

### Database (SQLite `state.db`)
- `feeds` table: RSS feeds with metadata
- `episodes` table: Episode tracking with processing status
- Status progression: `new → downloading → downloaded → transcribing → transcribed → analyzing → done`

### UI Architecture
- `PodcastTUI`: Main controller with view state management
- `UIRenderer`: Renders UI components and views
- `FeedProcessor`: Handles RSS feed population
- `EpisodeProcessor`: Manages episode processing pipeline
- `ExtendedDB`: Database with pagination and stats queries
- Processing runs in separate thread with status callbacks
- Uses Rich library for terminal rendering

### External Tool Integration
**Bundled as Git Submodules:**
- `tools/ai-cli/`: AI prompt executor (requires API keys)
- `tools/podcast-transcription/`: Local transcription tool

**Configuration:**
- Tools auto-detected from `tools/` directory
- Falls back to paths in `config.yaml` for manual installations
- Shell commands with placeholders:
  - **Transcription**: `{audio}` → `{transcript}`
  - **Insights**: `{transcript}` → `{insights_file}` with `--model` parameter
- Output captured to prevent UI pollution

**Requirements:**
- ffmpeg (checked during setup)
- API keys for OpenAI or Anthropic (configured via setup.sh)

### Storage Layout
```
data/
  <podcast_name_safe>/
    <YYYY-MM-DD>_<episode_title_safe>/
      <title>.mp3
      <title>.transcript.md
      <title>.insights.md
```

## Key Implementation Details

### Terminal UI
- Number input requires Enter key (no auto-execution)
- Command keys (ESC, q, l) execute immediately
- Processing view: fixed 70×12 panel with progress indicators
- Live updates via `rich.Live` with controlled refresh

### Thread Safety
- Each processing thread creates own database connection
- Status callbacks communicate between threads
- Subprocess output captured with `capture_output=True`

### Episode Population
- On startup, fetches ALL episodes from configured RSS feeds
- Stores in database for browsing (not auto-processing)
- User selects which episodes to process

## Testing Approach

### Setup Testing
1. Run `./setup.sh` and test both installation modes:
   - Automatic: Verify submodules clone and configure
   - Manual: Test path input and validation
2. Verify API key configuration and model selection
3. Check generated `config.yaml` has correct paths and model

### Application Testing
1. Launch with `./podcast-insights`
2. Navigate podcast list with number + Enter
3. Browse episodes, test pagination with 'l'
4. Process an episode and verify output in `data/`
5. Test ESC navigation and quit confirmation

### Update Testing
1. Run `./podcast-insights --update` to update tools
2. Verify submodules pull latest changes
3. Check dependency updates when requirements change

For UI changes, verify:
- Number input handling (single/multi-digit)
- Processing view updates cleanly
- Error messages display properly
- Subprocess output stays suppressed