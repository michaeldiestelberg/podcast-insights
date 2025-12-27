#!/usr/bin/env python3
"""Main application for Podcast Insights."""

import logging
import select
import sys
import termios
import threading
import time
import tty
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.prompt import Prompt

from database import ExtendedDB
from models import ProcessingMode, UIState
from processors import EpisodeProcessor, FeedProcessor
from ui_components import UIRenderer
from utils import load_config, parse_episode_selection, setup_logging


console = Console()


class PodcastTUI:
    """Main Terminal User Interface for Podcast Insights."""

    def __init__(self, config_path: Path):
        self.cfg = load_config(config_path)
        self.db = ExtendedDB(Path.cwd())
        self.ui_renderer = UIRenderer(self.db)
        self.state = UIState()
        self.console = Console()
        self.processing_status = None
        self.processing_title = None
        self.input_buffer = ""

        # Initialize feed processor and populate episodes
        feed_processor = FeedProcessor(self.cfg)
        feed_processor.populate_all_episodes()

        # Initialize episode processor for later use
        self.episode_processor = EpisodeProcessor(self.cfg, self.update_processing_status)

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
        """Update processing status callback."""
        self.processing_status = status
        self.processing_title = title

    def handle_podcast_selection(self, selection: str) -> bool:
        """Handle podcast selection from list."""
        try:
            idx = int(selection) - 1
            feeds = self.db.get_all_feeds_with_stats()

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
        """Handle episode selection - supports single and bulk selection."""
        # Get all episodes for the feed
        episodes = self.db.get_episodes_paginated(
            self.state.selected_feed_id,
            0,
            None
        )

        if not episodes:
            return False

        try:
            indices = parse_episode_selection(selection, len(episodes))
        except ValueError:
            return False

        if not indices:
            return False

        # Get selected episodes
        selected_episodes = [episodes[i] for i in indices]

        if len(selected_episodes) == 1:
            # Single episode - use existing action menu flow
            episode = selected_episodes[0]

            if episode["status"] == "done":
                self.console.print("[green]Episode already fully processed![/green]")
                time.sleep(1)
                return False

            self.state.selected_episode_id = episode["id"]
            self.state.processing_episode = episode
            self.processing_title = episode["title"]
            self.state.current_view = "action_menu"
            return True

        # Bulk selection - store all selected episodes
        self.state.bulk_episode_ids = [ep["id"] for ep in selected_episodes]
        self.state.bulk_episodes = selected_episodes
        self.state.bulk_current_index = 0
        self.state.bulk_completed_count = 0
        self.state.current_view = "bulk_action_menu"
        return True

    def handle_action_selection(self, key: str) -> bool:
        """Handle action selection from action menu."""
        episode = self.state.processing_episode
        status = episode["status"]

        if status in ("new", "error", "downloading", "downloaded", "transcribing"):
            if key == "1":
                self.state.processing_mode = ProcessingMode.FULL
                return True
            elif key == "2":
                self.state.processing_mode = ProcessingMode.TRANSCRIBE
                return True
        elif status == "transcribed":
            if key == "1":
                self.state.processing_mode = ProcessingMode.INSIGHTS
                return True

        return False

    def handle_bulk_action_selection(self, key: str) -> bool:
        """Handle action selection for bulk processing."""
        episodes = self.state.bulk_episodes

        # Categorize episodes by status
        processable = []  # new, error, downloading, downloaded, transcribing
        transcribed = []  # transcribed only
        done = []  # already done

        for ep in episodes:
            status = ep["status"]
            if status == "done":
                done.append(ep)
            elif status == "transcribed":
                transcribed.append(ep)
            else:
                processable.append(ep)

        # Determine mode and skipped based on key
        if key == "1":
            if processable:
                # Full processing mode
                self.state.processing_mode = ProcessingMode.FULL
                self.state.bulk_skipped_episodes = done
                return True
            elif transcribed:
                # Insights only mode (when only transcribed episodes)
                self.state.processing_mode = ProcessingMode.INSIGHTS
                self.state.bulk_skipped_episodes = done
                return True
        elif key == "2":
            if processable:
                # Transcribe only mode
                self.state.processing_mode = ProcessingMode.TRANSCRIBE
                # Skip done and already transcribed
                self.state.bulk_skipped_episodes = done + transcribed
                return True

        return False

    def load_more_episodes(self):
        """Load more episodes in the list."""
        self.console.clear()

        total = self.db.get_total_episodes_count(self.state.selected_feed_id)
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
        """Process selected episode based on selected mode."""
        if not self.state.selected_episode_id:
            return

        # Clear the console once at the start
        self.console.clear()

        mode = self.state.processing_mode or ProcessingMode.FULL
        mode_str = mode.value

        # Initialize processing status based on mode
        if mode == ProcessingMode.INSIGHTS:
            self.processing_status = "analyzing"
        else:
            self.processing_status = "downloading"

        # Run processing in a separate thread
        success = False
        error_msg = None

        def process():
            nonlocal success, error_msg
            if mode == ProcessingMode.FULL:
                success, error_msg = self.episode_processor.process_single_episode(
                    self.state.selected_episode_id
                )
            elif mode == ProcessingMode.TRANSCRIBE:
                success, error_msg = self.episode_processor.process_transcribe_only(
                    self.state.selected_episode_id
                )
            elif mode == ProcessingMode.INSIGHTS:
                success, error_msg = self.episode_processor.process_insights_only(
                    self.state.selected_episode_id
                )

        thread = threading.Thread(target=process)
        thread.start()

        # Use Live for smooth updates with mode-aware rendering
        with Live(
            self.ui_renderer.render_processing(self.processing_status, self.processing_title, processing_mode=mode_str),
            console=self.console,
            refresh_per_second=2,
            vertical_overflow="visible"
        ) as live:
            # Update while processing
            while thread.is_alive():
                live.update(self.ui_renderer.render_processing(
                    self.processing_status,
                    self.processing_title,
                    self.state.error_message,
                    processing_mode=mode_str
                ))
                time.sleep(0.5)

            thread.join()

            # Show final status
            if success:
                self.processing_status = "done"
            else:
                self.processing_status = "error"
                self.state.error_message = error_msg

            live.update(self.ui_renderer.render_processing(
                self.processing_status,
                self.processing_title,
                self.state.error_message,
                processing_mode=mode_str
            ))
            time.sleep(2)

        # Return to episode list
        self.state.current_view = "episode_list"
        self.state.selected_episode_id = None
        self.state.processing_episode = None
        self.processing_status = None
        self.processing_title = None
        self.state.error_message = None
        self.state.processing_mode = None

    def process_bulk_episodes(self):
        """Process multiple episodes sequentially."""
        if not self.state.bulk_episode_ids:
            return

        self.console.clear()

        mode = self.state.processing_mode or ProcessingMode.FULL
        mode_str = mode.value
        total = len(self.state.bulk_episode_ids)

        for idx, episode_id in enumerate(self.state.bulk_episode_ids):
            self.state.bulk_current_index = idx

            # Get current episode (refresh from DB for latest status)
            episode = self.db.get_episode_by_id(episode_id)
            self.state.processing_episode = episode
            self.processing_title = episode["title"]

            # Skip if not compatible with mode
            should_skip = False
            if mode == ProcessingMode.FULL and episode["status"] == "done":
                should_skip = True
            elif mode == ProcessingMode.TRANSCRIBE and episode["status"] in ("done", "transcribed"):
                should_skip = True
            elif mode == ProcessingMode.INSIGHTS and episode["status"] != "transcribed":
                should_skip = True

            if should_skip:
                continue

            # Initialize status
            if mode == ProcessingMode.INSIGHTS:
                self.processing_status = "analyzing"
            else:
                self.processing_status = "downloading"

            # Run processing in thread
            success = False
            error_msg = None

            def process():
                nonlocal success, error_msg
                if mode == ProcessingMode.FULL:
                    success, error_msg = self.episode_processor.process_single_episode(episode_id)
                elif mode == ProcessingMode.TRANSCRIBE:
                    success, error_msg = self.episode_processor.process_transcribe_only(episode_id)
                elif mode == ProcessingMode.INSIGHTS:
                    success, error_msg = self.episode_processor.process_insights_only(episode_id)

            thread = threading.Thread(target=process)
            thread.start()

            # Live update for this episode
            with Live(
                self.ui_renderer.render_bulk_processing(
                    episode, self.processing_status, idx, total,
                    self.state.bulk_completed_count, mode_str
                ),
                console=self.console,
                refresh_per_second=2
            ) as live:
                while thread.is_alive():
                    live.update(self.ui_renderer.render_bulk_processing(
                        episode, self.processing_status, idx, total,
                        self.state.bulk_completed_count, mode_str,
                        self.state.error_message
                    ))
                    time.sleep(0.5)

                thread.join()

                if success:
                    self.state.bulk_completed_count += 1
                    self.processing_status = "done"
                else:
                    self.processing_status = "error"
                    self.state.error_message = error_msg

                # Brief pause to show result
                live.update(self.ui_renderer.render_bulk_processing(
                    episode, self.processing_status, idx, total,
                    self.state.bulk_completed_count, mode_str,
                    self.state.error_message
                ))
                time.sleep(1)

            # Reset error message for next episode
            self.state.error_message = None

        # Show final summary
        self._show_bulk_complete_summary()

        # Reset state
        self._reset_bulk_state()

    def _show_bulk_complete_summary(self):
        """Show summary after bulk processing completes."""
        from rich.panel import Panel
        from rich.text import Text

        total = len(self.state.bulk_episode_ids)
        completed = self.state.bulk_completed_count
        skipped = total - completed

        self.console.clear()
        style = "green" if skipped == 0 else "yellow"
        summary = Panel(
            Text(
                f"Bulk processing complete!\n\n"
                f"Completed: {completed}/{total}\n"
                f"Skipped/Failed: {skipped}",
                style=style
            ),
            title="Summary",
            border_style=style
        )
        self.console.print(summary)
        time.sleep(2)

    def _reset_bulk_state(self):
        """Reset bulk processing state."""
        self.state.current_view = "episode_list"
        self.state.bulk_episode_ids = None
        self.state.bulk_episodes = None
        self.state.bulk_current_index = 0
        self.state.bulk_completed_count = 0
        self.state.bulk_skipped_episodes = None
        self.state.selected_episode_id = None
        self.state.processing_episode = None
        self.processing_status = None
        self.processing_title = None
        self.state.error_message = None
        self.state.processing_mode = None

    def confirm_quit(self) -> bool:
        """Show quit confirmation dialog."""
        self.console.clear()
        self.console.print(self.ui_renderer.render_quit_confirmation())

        while True:
            key = self.getch()
            if key == '\r' or key == '\n':  # Enter
                return True
            elif key == 'ESC' or key == '\x1b':  # ESC
                return False

    def run(self):
        """Main run loop for the TUI."""
        self.console.clear()

        while True:
            try:
                if self.state.current_view == "podcast_list":
                    self.console.clear()
                    self.console.print(self.ui_renderer.render_podcast_list())
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
                    self.console.print(self.ui_renderer.render_episode_list(
                        self.state.selected_feed_id,
                        self.state.selected_feed_name,
                        self.state.episode_offset,
                        self.state.episode_limit
                    ))
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
                    elif key.isdigit() or key == 'a':
                        # Start collecting input - supports numbers, ranges, and "all"
                        self.input_buffer = key
                        self.console.print(key, end="")

                        # Keep collecting valid characters until Enter
                        while True:
                            next_key = self.getch()
                            if next_key.isdigit() or next_key in (',', '-'):
                                # Add digit, comma, or hyphen to buffer
                                self.input_buffer += next_key
                                self.console.print(next_key, end="")
                            elif next_key.lower() == 'l' and self.input_buffer.lower() == 'a':
                                # Complete "al" for "all"
                                self.input_buffer += next_key
                                self.console.print(next_key, end="")
                            elif next_key.lower() == 'l' and self.input_buffer.lower() == 'al':
                                # Complete "all"
                                self.input_buffer += next_key
                                self.console.print(next_key, end="")
                            elif next_key == '\r' or next_key == '\n':
                                # Enter pressed - execute selection
                                self.handle_episode_selection(self.input_buffer)
                                break
                            elif next_key == '\x7f' or next_key == '\b':  # Backspace
                                # Handle backspace
                                if len(self.input_buffer) > 0:
                                    self.input_buffer = self.input_buffer[:-1]
                                    self.console.print('\b \b', end="")
                                if not self.input_buffer:
                                    break
                            elif next_key == 'ESC' or next_key == '\x1b':
                                # ESC cancels input
                                break
                            else:
                                # Any other key cancels input
                                break

                        self.input_buffer = ""

                elif self.state.current_view == "action_menu":
                    self.console.clear()
                    self.console.print(self.ui_renderer.render_action_menu(
                        self.state.processing_episode
                    ))

                    key = self.getch()

                    if key == 'ESC' or key == '\x1b':
                        # Cancel - return to episode list
                        self.state.current_view = "episode_list"
                        self.state.selected_episode_id = None
                        self.state.processing_episode = None
                        self.state.processing_mode = None
                    elif key == 'q':
                        if self.confirm_quit():
                            break
                    elif key in ('1', '2'):
                        if self.handle_action_selection(key):
                            self.state.current_view = "processing"
                            self.process_episode()

                elif self.state.current_view == "processing":
                    # Processing is handled when transitioning to this view
                    # Just wait here until processing is done
                    pass

                elif self.state.current_view == "bulk_action_menu":
                    self.console.clear()
                    self.console.print(self.ui_renderer.render_bulk_action_menu(
                        self.state.bulk_episodes
                    ))

                    key = self.getch()

                    if key == 'ESC' or key == '\x1b':
                        self._reset_bulk_state()
                        self.state.current_view = "episode_list"
                    elif key == 'q':
                        if self.confirm_quit():
                            break
                    elif key in ('1', '2'):
                        if self.handle_bulk_action_selection(key):
                            # Check if confirmation needed
                            if self.state.bulk_skipped_episodes:
                                self.state.current_view = "bulk_confirm"
                            else:
                                self.state.current_view = "bulk_processing"
                                self.process_bulk_episodes()

                elif self.state.current_view == "bulk_confirm":
                    self.console.clear()
                    # Get episodes that will be processed
                    skipped_ids = set(ep["id"] for ep in (self.state.bulk_skipped_episodes or []))
                    processing_episodes = [
                        ep for ep in self.state.bulk_episodes
                        if ep["id"] not in skipped_ids
                    ]
                    self.console.print(self.ui_renderer.render_skip_confirmation(
                        self.state.bulk_skipped_episodes or [],
                        processing_episodes
                    ))

                    key = self.getch()

                    if key == 'ESC' or key == '\x1b':
                        self._reset_bulk_state()
                        self.state.current_view = "episode_list"
                    elif key == '\r' or key == '\n':
                        # Filter to only processable episodes
                        self.state.bulk_episode_ids = [ep["id"] for ep in processing_episodes]
                        self.state.bulk_episodes = processing_episodes
                        self.state.current_view = "bulk_processing"
                        self.process_bulk_episodes()

            except KeyboardInterrupt:
                if self.state.current_view in ("action_menu", "bulk_action_menu", "bulk_confirm"):
                    # Go back to episode list
                    self._reset_bulk_state()
                    self.state.current_view = "episode_list"
                    self.state.selected_episode_id = None
                    self.state.processing_episode = None
                    self.state.processing_mode = None
                elif self.state.current_view == "episode_list":
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
    """Main entry point."""
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