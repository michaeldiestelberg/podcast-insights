#!/usr/bin/env python3
import datetime as dt
import logging
import select
import sqlite3
import sys
import termios
import threading
import time
import tty
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import yaml
from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.spinner import Spinner
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from watcher import (
    AppConfig,
    DB,
    Watcher,
    ensure_dir,
    entry_guid,
    entry_pubdate,
    entry_datetime,
    iso_now,
    load_config,
    safe_name,
    select_enclosure,
    setup_logging,
)


console = Console()


@dataclass
class UIState:
    current_view: str = "podcast_list"
    selected_feed_id: Optional[int] = None
    selected_feed_name: Optional[str] = None
    selected_episode_id: Optional[int] = None
    episode_offset: int = 0
    episode_limit: int = 5
    processing_episode: Optional[sqlite3.Row] = None
    error_message: Optional[str] = None


class ExtendedDB(DB):
    def get_all_feeds_with_stats(self) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT
                f.id,
                f.name,
                f.slug,
                f.url,
                COUNT(CASE WHEN e.status = 'new' THEN 1 END) as new_count,
                COUNT(CASE WHEN e.status = 'done' THEN 1 END) as done_count,
                COUNT(e.id) as total_count
            FROM feeds f
            LEFT JOIN episodes e ON f.id = e.feed_id
            GROUP BY f.id
            ORDER BY f.name
        """)
        return [dict(row) for row in cur.fetchall()]

    def get_episodes_paginated(self, feed_id: int, offset: int = 0, limit: Optional[int] = None) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        query = """
            SELECT * FROM episodes
            WHERE feed_id = ?
            ORDER BY
                CASE WHEN pub_date IS NOT NULL THEN pub_date ELSE '9999-99-99' END DESC,
                first_seen_at DESC
        """
        params = [feed_id]

        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        cur.execute(query, params)
        return cur.fetchall()

    def get_total_episodes_count(self, feed_id: int) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM episodes WHERE feed_id = ?", (feed_id,))
        return cur.fetchone()[0]


class InteractiveWatcher(Watcher):
    def __init__(self, cfg: AppConfig, status_callback=None):
        super().__init__(cfg)
        self.db = ExtendedDB(Path.cwd())
        self.status_callback = status_callback
        # Populate all episodes on startup for interactive browsing
        self.populate_all_episodes()

    def populate_all_episodes(self):
        """Populate database with all episodes from RSS feeds for browsing."""
        for feed_cfg in self.cfg.feeds:
            try:
                # Get or create feed in database
                feed_title = feed_cfg.name or feed_cfg.url
                feed_slug = safe_name(feed_title)
                feed_id = self.db.upsert_feed(feed_cfg.url, feed_title, feed_slug)

                # Fetch RSS feed
                etag, last_mod = self.db.fetch_feed_http_cache(feed_cfg.url)
                parsed = feedparser.parse(feed_cfg.url, etag=etag, modified=last_mod)

                # Skip if not modified
                if getattr(parsed, "status", None) == 304:
                    continue

                # Update feed metadata
                new_etag = getattr(parsed, "etag", None)
                new_modified = getattr(parsed, "modified", None)
                self.db.update_feed_http(feed_id, new_etag, new_modified)

                # Build feed directory
                feed_dir = self.cfg.storage.data_dir / feed_slug
                ensure_dir(feed_dir)

                # Process all entries (not just max_new_per_feed)
                entries = list(getattr(parsed, "entries", []))
                if not entries:
                    continue

                # Sort newest first
                try:
                    import datetime as dt
                    entries.sort(
                        key=lambda e: entry_datetime(e) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                        reverse=True
                    )
                except Exception:
                    pass

                # Add ALL episodes to database (not just new ones to process)
                for entry in entries:
                    guid = entry_guid(entry)
                    audio = select_enclosure(entry)
                    title = getattr(entry, "title", None) or "Untitled Episode"
                    pubdate = entry_pubdate(entry)

                    if not audio:
                        continue

                    # Check if already exists
                    existing = self.db.find_episode(feed_id, guid, audio)
                    if existing:
                        continue

                    # Compute paths (but don't create files)
                    episode_dir_name = f"{pubdate}_{safe_name(title)}"
                    episode_dir = feed_dir / episode_dir_name
                    audio_file = f"{safe_name(title)}.mp3"
                    transcript_file = f"{safe_name(title)}.transcript.md"
                    insights_file = f"{safe_name(title)}.insights.md"

                    audio_path = episode_dir / audio_file
                    transcript_path = episode_dir / transcript_file
                    insights_path = episode_dir / insights_file

                    # Insert episode as 'new' (available for browsing)
                    self.db.insert_episode(
                        feed_id=feed_id,
                        guid=guid,
                        audio_url=audio,
                        title=title,
                        pub_date=pubdate,
                        episode_dir=episode_dir,
                        audio_path=audio_path,
                        transcript_path=transcript_path,
                        insights_path=insights_path,
                    )

            except Exception as e:
                logging.warning(f"Error populating episodes for {feed_cfg.url}: {e}")

    def _run_transcription(self, audio_path: Path, transcript_path: Path) -> None:
        """Override to suppress output in interactive mode."""
        import subprocess
        cmd = self.cfg.tools.transcribe_cmd.format(
            audio=str(audio_path), transcript=str(transcript_path)
        )
        logging.info("Transcribing: %s", audio_path)
        # Capture and discard subprocess output
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,  # Capture stdout and stderr
            text=True
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Transcription failed: {proc.stderr}")
        logging.info("Transcription done: %s", transcript_path)

    def _run_insights(self, transcript_path: Path, insights_path: Path) -> None:
        """Override to suppress output in interactive mode."""
        import subprocess
        episode_dir = str(insights_path.parent)
        insights_file = insights_path.name
        cmd = self.cfg.tools.insights_cmd.format(
            transcript=str(transcript_path), episode_dir=episode_dir, insights_file=insights_file
        )
        logging.info("Extracting insights: %s", insights_path)
        # Capture and discard subprocess output
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,  # Capture stdout and stderr
            text=True
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Insights extraction failed: {proc.stderr}")
        logging.info("Insights extraction done: %s", insights_path)

    def process_single_episode(self, episode_id: int) -> Tuple[bool, Optional[str]]:
        # Create a new DB connection for this thread
        thread_db = ExtendedDB(Path.cwd())

        try:
            row = thread_db.get_episode_by_id(episode_id)

            if self.status_callback:
                self.status_callback("downloading", row["title"])

            episode_dir = Path(row["episode_dir"])
            audio_path = Path(row["audio_path"])
            transcript_path = Path(row["transcript_path"])
            insights_path = Path(row["insights_path"])

            ensure_dir(episode_dir)

            # Download
            if not audio_path.exists():
                thread_db.update_episode_status(episode_id, "downloading")
                self._download_audio(row["audio_url"], audio_path)
            thread_db.update_episode_status(episode_id, "downloaded")

            if self.status_callback:
                self.status_callback("transcribing", row["title"])

            # Transcribe
            if not transcript_path.exists():
                thread_db.update_episode_status(episode_id, "transcribing")
                self._run_transcription(audio_path, transcript_path)
                if not transcript_path.exists():
                    raise RuntimeError(f"Transcription did not produce expected file: {transcript_path}")
            thread_db.update_episode_status(episode_id, "transcribed")

            if self.status_callback:
                self.status_callback("analyzing", row["title"])

            # Insights
            if not insights_path.exists():
                thread_db.update_episode_status(episode_id, "analyzing")
                self._run_insights(transcript_path, insights_path)
                if not insights_path.exists():
                    raise RuntimeError(f"Insights extraction did not produce expected file: {insights_path}")

            thread_db.update_episode_status(episode_id, "done")

            if self.status_callback:
                self.status_callback("done", row["title"])

            return True, None

        except Exception as e:
            error_msg = str(e)
            logging.error(f"Error processing episode {episode_id}: {error_msg}")
            try:
                thread_db.update_episode_status(episode_id, "error", error_msg)
            except:
                pass  # Ignore if we can't update status due to connection issues
            return False, error_msg
        finally:
            # Close the thread-local database connection
            if 'thread_db' in locals():
                thread_db.conn.close()


class PodcastTUI:
    def __init__(self, config_path: Path):
        self.cfg = load_config(config_path)
        self.watcher = InteractiveWatcher(self.cfg, self.update_processing_status)
        self.state = UIState()
        self.console = Console()
        self.processing_status = None
        self.processing_title = None
        self.input_buffer = ""

    def getch(self) -> str:
        """Get a single character from user input without pressing Enter."""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
            # Handle special keys
            if ch == '\x1b':  # ESC sequence
                # Check if more characters are available (for arrow keys)
                if select.select([sys.stdin], [], [], 0)[0]:
                    ch2 = sys.stdin.read(1)
                    if ch2 == '[':
                        # Read one more for arrow keys
                        ch3 = sys.stdin.read(1)
                        if ch3 == 'A':  # Up arrow
                            return 'UP'
                        elif ch3 == 'B':  # Down arrow
                            return 'DOWN'
                # Just ESC key pressed
                return 'ESC'
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def update_processing_status(self, status: str, title: str):
        self.processing_status = status
        self.processing_title = title

    def get_status_symbol(self, status: str) -> str:
        symbols = {
            "new": "[ ]",
            "downloading": "[↓]",
            "downloaded": "[↓]",
            "transcribing": "[T]",
            "transcribed": "[T]",
            "analyzing": "[I]",
            "done": "[✓]",
            "error": "[!]",
            "error_download": "[!]",
            "error_transcribe": "[!]",
            "error_insights": "[!]",
        }
        return symbols.get(status, "[?]")

    def get_status_color(self, status: str) -> str:
        colors = {
            "new": "white",
            "downloading": "yellow",
            "downloaded": "yellow",
            "transcribing": "cyan",
            "transcribed": "cyan",
            "analyzing": "magenta",
            "done": "green",
            "error": "red",
            "error_download": "red",
            "error_transcribe": "red",
            "error_insights": "red",
        }
        return colors.get(status, "white")

    def render_podcast_list(self) -> Panel:
        table = Table(title="Podcast Library", show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=4)
        table.add_column("Podcast", style="cyan", no_wrap=False)
        table.add_column("New", justify="right", style="yellow")
        table.add_column("Processed", justify="right", style="green")
        table.add_column("Total", justify="right")

        feeds = self.watcher.db.get_all_feeds_with_stats()

        for idx, feed in enumerate(feeds, 1):
            table.add_row(
                str(idx),
                feed["name"] or feed["slug"] or "Unnamed",
                str(feed["new_count"]),
                str(feed["done_count"]),
                str(feed["total_count"])
            )

        return Panel(
            table,
            title="📻 Podcast Insights",
            border_style="blue"
        )

    def render_episode_list(self) -> Panel:
        feed_name = self.state.selected_feed_name or "Unknown Podcast"

        table = Table(
            title=f"Episodes - {feed_name}",
            show_header=True,
            header_style="bold magenta"
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Status", width=6)
        table.add_column("Episode Title", no_wrap=False)
        table.add_column("Date", width=12)

        episodes = self.watcher.db.get_episodes_paginated(
            self.state.selected_feed_id,
            self.state.episode_offset,
            self.state.episode_limit
        )

        total_episodes = self.watcher.db.get_total_episodes_count(self.state.selected_feed_id)

        for idx, episode in enumerate(episodes, 1 + self.state.episode_offset):
            status = episode["status"]
            symbol = self.get_status_symbol(status)
            color = self.get_status_color(status)

            table.add_row(
                str(idx),
                f"[{color}]{symbol}[/{color}]",
                episode["title"] or "Untitled",
                episode["pub_date"] or "Unknown"
            )

        showing_text = f"Showing {self.state.episode_offset + 1}-{min(self.state.episode_offset + self.state.episode_limit, total_episodes)} of {total_episodes} episodes"

        help_text = Text(
            f"\n{showing_text}\n[Number+Enter] Select episode  [l] Load more  [ESC] Back  [q] Quit",
            style="dim"
        )

        return Panel(
            Group(table, help_text),
            title=f"🎧 {feed_name}",
            border_style="cyan"
        )

    def render_processing(self) -> Panel:
        from rich.table import Table
        from rich.align import Align

        # Create a compact table for progress
        table = Table.grid(padding=1)
        table.add_column(justify="left")

        # Add episode title (truncated for space)
        if self.processing_title:
            title_text = self.processing_title[:60]
            if len(self.processing_title) > 60:
                title_text += "..."
            table.add_row(Text(f"📻 {title_text}", style="bold white"))
            table.add_row("")  # Spacing

        # Add current status
        if self.processing_status == "done":
            table.add_row(Text("✅ Processing complete!", style="bold green"))
            table.add_row(Text("Returning to episode list...", style="dim italic"))
        elif self.processing_status == "error":
            table.add_row(Text("❌ Error occurred", style="bold red"))
            if self.state.error_message:
                error_text = self.state.error_message[:80]
                if len(self.state.error_message) > 80:
                    error_text += "..."
                table.add_row(Text(error_text, style="red dim"))
        else:
            # Show progress steps
            steps = [
                ("downloading", "📥 Download audio", "yellow"),
                ("transcribing", "📝 Transcribe audio", "cyan"),
                ("analyzing", "🧠 Extract insights", "magenta")
            ]

            for step_id, step_text, step_color in steps:
                if self.processing_status == step_id:
                    # Current step - animated
                    from rich.spinner import Spinner
                    from rich.columns import Columns
                    spinner = Spinner("dots", style=step_color)
                    text = Text(f" {step_text}...", style=f"bold {step_color}")
                    table.add_row(Columns([spinner, text], padding=0))
                elif self._is_step_complete(step_id):
                    # Completed step
                    table.add_row(Text(f"  ✓ {step_text}", style="green dim"))
                else:
                    # Future step
                    table.add_row(Text(f"  ○ {step_text}", style="dim"))

            table.add_row("")  # Spacing
            table.add_row(Text("[Ctrl+C] Cancel", style="dim italic"))

        return Panel(
            Align.center(table, vertical="middle"),
            title="⚡ Processing Episode",
            border_style="yellow",
            height=12
        )

    def _is_step_complete(self, step: str) -> bool:
        """Check if a processing step is complete based on current status."""
        steps_order = ["downloading", "transcribing", "analyzing", "done"]
        if self.processing_status in steps_order:
            current_idx = steps_order.index(self.processing_status)
            step_idx = steps_order.index(step) if step in steps_order else -1
            return step_idx < current_idx
        return False

    def handle_podcast_selection(self, selection: str) -> bool:
        try:
            idx = int(selection) - 1
            feeds = self.watcher.db.get_all_feeds_with_stats()

            if 0 <= idx < len(feeds):
                feed = feeds[idx]
                self.state.selected_feed_id = feed["id"]
                self.state.selected_feed_name = feed["name"] or feed["slug"]
                self.state.current_view = "episode_list"
                self.state.episode_offset = 0
                self.state.episode_limit = 5
                return True
        except (ValueError, IndexError):
            pass

        return False

    def handle_episode_selection(self, selection: str) -> bool:
        try:
            idx = int(selection) - 1
            episodes = self.watcher.db.get_episodes_paginated(
                self.state.selected_feed_id,
                0,
                None
            )

            if 0 <= idx < len(episodes):
                episode = episodes[idx]

                if episode["status"] == "done":
                    self.console.print("[green]Episode already processed![/green]")
                    time.sleep(1)
                    return False

                self.state.selected_episode_id = episode["id"]
                self.state.processing_episode = episode
                self.processing_title = episode["title"]
                # Don't change view here, let the caller handle it
                return True
        except (ValueError, IndexError):
            pass

        return False

    def load_more_episodes(self):
        self.console.clear()

        total = self.watcher.db.get_total_episodes_count(self.state.selected_feed_id)
        current_showing = self.state.episode_offset + self.state.episode_limit

        if current_showing >= total:
            self.console.print("[yellow]All episodes are already loaded![/yellow]")
            time.sleep(1)
            return

        prompt = Prompt.ask(
            "Load how many more episodes? (number/all)",
            default="5"
        )

        if prompt.lower() == "all":
            self.state.episode_limit = total
        else:
            try:
                additional = int(prompt)
                if additional > 0:
                    self.state.episode_limit = min(
                        self.state.episode_offset + self.state.episode_limit + additional,
                        total
                    )
            except ValueError:
                self.console.print("[red]Invalid input! Using default (5)[/red]")
                self.state.episode_limit += 5

    def process_episode(self):
        if not self.state.selected_episode_id:
            return

        # Clear the console once at the start
        self.console.clear()

        # Initialize processing status
        self.processing_status = "downloading"

        # Run processing in a separate thread
        success = False
        error_msg = None

        def process():
            nonlocal success, error_msg
            success, error_msg = self.watcher.process_single_episode(
                self.state.selected_episode_id
            )

        thread = threading.Thread(target=process)
        thread.start()

        # Use Live for smooth updates
        from rich.live import Live

        with Live(
            self.render_processing(),
            console=self.console,
            refresh_per_second=2,
            vertical_overflow="visible"
        ) as live:
            # Update while processing
            while thread.is_alive():
                live.update(self.render_processing())
                time.sleep(0.5)

            thread.join()

            # Show final status
            if success:
                self.processing_status = "done"
            else:
                self.processing_status = "error"
                self.state.error_message = error_msg

            live.update(self.render_processing())
            time.sleep(2)

        # Return to episode list
        self.state.current_view = "episode_list"
        self.state.selected_episode_id = None
        self.state.processing_episode = None
        self.processing_status = None
        self.processing_title = None
        self.state.error_message = None

    def confirm_quit(self) -> bool:
        """Show quit confirmation dialog."""
        self.console.clear()
        self.console.print(Panel(
            Text("Are you sure you want to quit?\n\n[Enter] Confirm  [ESC] Cancel", style="yellow"),
            title="⚠️ Confirm Quit",
            border_style="yellow"
        ))

        while True:
            key = self.getch()
            if key == '\r' or key == '\n':  # Enter
                return True
            elif key == 'ESC' or key == '\x1b':  # ESC
                return False

    def run(self):
        self.console.clear()

        while True:
            try:
                if self.state.current_view == "podcast_list":
                    self.console.clear()
                    self.console.print(self.render_podcast_list())
                    self.console.print("\n[bold cyan]Enter podcast number or \\[q] to quit: [/bold cyan]", end="")

                    key = self.getch()

                    if key == 'q':
                        if self.confirm_quit():
                            break
                    elif key.isdigit() and key != '0':
                        # Start collecting number input - require Enter
                        self.input_buffer = key
                        self.console.print(key, end="")

                        # Keep collecting digits until Enter
                        while True:
                            next_key = self.getch()
                            if next_key.isdigit():
                                self.input_buffer += next_key
                                self.console.print(next_key, end="")
                            elif next_key == '\r' or next_key == '\n':
                                # Enter pressed - execute selection
                                self.handle_podcast_selection(self.input_buffer)
                                break
                            elif next_key == '\x7f' or next_key == '\b':  # Backspace
                                if len(self.input_buffer) > 0:
                                    self.input_buffer = self.input_buffer[:-1]
                                    self.console.print('\b \b', end="")
                                if not self.input_buffer:
                                    break
                            elif next_key == 'ESC' or next_key == '\x1b':
                                # ESC cancels number input
                                break
                            else:
                                # Any other key cancels input
                                break

                        self.input_buffer = ""

                elif self.state.current_view == "episode_list":
                    self.console.clear()
                    self.console.print(self.render_episode_list())
                    self.console.print("\n[bold cyan]Enter episode number, \\[l] for more, \\[ESC] to go back: [/bold cyan]", end="")

                    key = self.getch()

                    if key == 'q':
                        if self.confirm_quit():
                            break
                    elif key == 'ESC' or key == '\x1b':
                        self.state.current_view = "podcast_list"
                        self.state.selected_feed_id = None
                        self.state.selected_feed_name = None
                        self.input_buffer = ""
                    elif key == 'l':
                        self.load_more_episodes()
                    elif key.isdigit() and key != '0':
                        # Start collecting number input - ALWAYS require Enter
                        self.input_buffer = key
                        self.console.print(key, end="")

                        # Keep collecting digits until Enter
                        while True:
                            next_key = self.getch()
                            if next_key.isdigit():
                                # Add digit to buffer
                                self.input_buffer += next_key
                                self.console.print(next_key, end="")
                            elif next_key == '\r' or next_key == '\n':
                                # Enter pressed - execute selection
                                if self.handle_episode_selection(self.input_buffer):
                                    self.state.current_view = "processing"
                                    self.process_episode()
                                    # process_episode handles returning to episode list
                                break
                            elif next_key == '\x7f' or next_key == '\b':  # Backspace
                                # Handle backspace
                                if len(self.input_buffer) > 0:
                                    self.input_buffer = self.input_buffer[:-1]
                                    self.console.print('\b \b', end="")
                                if not self.input_buffer:
                                    break
                            elif next_key == 'ESC' or next_key == '\x1b':
                                # ESC cancels number input
                                break
                            else:
                                # Any other key cancels input
                                break

                        self.input_buffer = ""

                elif self.state.current_view == "processing":
                    # Processing is handled when transitioning to this view
                    # Just wait here until processing is done
                    pass

            except KeyboardInterrupt:
                if self.state.current_view == "episode_list":
                    self.state.current_view = "podcast_list"
                    self.state.selected_feed_id = None
                    self.state.selected_feed_name = None
                else:
                    if self.confirm_quit():
                        break
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/red]")
                time.sleep(2)

        self.console.clear()
        self.console.print("[green]Thank you for using Podcast Insights![/green]")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Podcast Insights")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        console.print("[yellow]Create config.yaml from config.example.yaml to get started[/yellow]")
        sys.exit(1)

    setup_logging(Path.cwd())

    # Suppress logging output for clean interface
    logging.getLogger().setLevel(logging.ERROR)

    app = PodcastTUI(config_path)
    app.run()


if __name__ == "__main__":
    main()