# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Core Commands

### Running the Application
```bash
# Launch the application
./podcast-insights

# With custom config
./podcast-insights --config path/to/config.yaml
```

### Initial Setup
```bash
# Copy and configure
cp config.example.yaml config.yaml
# Edit config.yaml with:
# - Paths to transcription tool (local-podcast-transcription/transcribe.sh)
# - Paths to insights tool (ai-cli/ai-prompt)
# - RSS feed URLs
```

## Architecture Overview

### Application Structure
Single-mode interactive terminal UI for browsing and processing podcast episodes.

**Core Modules:**
- `interactive.py`: Main application with terminal UI
- `watcher.py`: Helper functions, database operations, download/processing logic
- `podcast-insights`: Shell wrapper managing virtualenv

### Processing Pipeline
User-initiated sequential pipeline: **Select Episode → Download → Transcribe → Extract Insights**

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
- `InteractiveWatcher`: Extended watcher with UI callbacks and subprocess suppression
- `ExtendedDB`: Database with pagination and stats queries
- Processing runs in separate thread with status callbacks
- Uses Rich library for terminal rendering

### External Tool Integration
Shell commands with placeholders:
- **Transcription**: `{audio}` → `{transcript}`
- **Insights**: `{transcript}` → `{insights_file}`
- Output captured to prevent UI pollution

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

Manual testing workflow:
1. Launch with `./podcast-insights`
2. Navigate podcast list with number + Enter
3. Browse episodes, test pagination with 'l'
4. Process an episode and verify output in `data/`
5. Test ESC navigation and quit confirmation

For UI changes, verify:
- Number input handling (single/multi-digit)
- Processing view updates cleanly
- Error messages display properly
- Subprocess output stays suppressed