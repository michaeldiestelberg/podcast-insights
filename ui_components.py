#!/usr/bin/env python3
"""UI components for Podcast Insights TUI."""

from rich.align import Align
from rich.console import Group
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.columns import Columns

from database import ExtendedDB


class UIRenderer:
    """Renders UI components for the TUI."""

    def __init__(self, db: ExtendedDB):
        self.db = db

    @staticmethod
    def get_status_symbol(status: str) -> str:
        """Get symbol for episode status."""
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

    @staticmethod
    def get_status_color(status: str) -> str:
        """Get color for episode status."""
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
        """Render the podcast list view."""
        table = Table(title="Podcast Library", show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=4)
        table.add_column("Podcast", style="cyan", no_wrap=False)
        table.add_column("New", justify="right", style="yellow")
        table.add_column("Processed", justify="right", style="green")
        table.add_column("Total", justify="right")

        feeds = self.db.get_all_feeds_with_stats()

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

    def render_episode_list(self, feed_id: int, feed_name: str, offset: int, limit: int) -> Panel:
        """Render the episode list view."""
        table = Table(
            title=f"Episodes - {feed_name}",
            show_header=True,
            header_style="bold magenta"
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Status", width=6)
        table.add_column("Episode Title", no_wrap=False)
        table.add_column("Date", width=12)

        episodes = self.db.get_episodes_paginated(feed_id, offset, limit)
        total_episodes = self.db.get_total_episodes_count(feed_id)

        for idx, episode in enumerate(episodes, 1 + offset):
            status = episode["status"]
            symbol = self.get_status_symbol(status)
            color = self.get_status_color(status)

            table.add_row(
                str(idx),
                f"[{color}]{symbol}[/{color}]",
                episode["title"] or "Untitled",
                episode["pub_date"] or "Unknown"
            )

        showing_text = f"Showing {offset + 1}-{min(offset + limit, total_episodes)} of {total_episodes} episodes"

        help_text = Text(
            f"\n{showing_text}\n[1,3,5] or [1-5] or [all] Bulk select  [l] Load more  [ESC] Back  [q] Quit",
            style="dim"
        )

        return Panel(
            Group(table, help_text),
            title=f"🎧 {feed_name}",
            border_style="cyan"
        )

    def render_action_menu(self, episode: dict) -> Panel:
        """Render action menu for selected episode."""
        status = episode["status"]
        title = episode["title"]
        if len(title) > 50:
            title = title[:50] + "..."

        table = Table.grid(padding=1)
        table.add_column(justify="left")

        table.add_row(Text(f"Episode: {title}", style="bold cyan"))
        table.add_row(Text(f"Status: {status}", style="dim"))
        table.add_row("")  # Spacing
        table.add_row(Text("Select action:", style="bold"))

        # Show options based on status
        if status in ("new", "error", "downloading", "downloaded"):
            table.add_row(Text("[1] Full processing (download + transcribe + insights)", style="white"))
            table.add_row(Text("[2] Transcribe only (download + transcribe)", style="white"))
        elif status in ("transcribing",):
            table.add_row(Text("[1] Resume full processing", style="white"))
            table.add_row(Text("[2] Transcribe only", style="white"))
        elif status == "transcribed":
            table.add_row(Text("[1] Extract insights", style="white"))

        table.add_row("")
        table.add_row(Text("[ESC] Cancel", style="dim italic"))

        return Panel(
            Align.center(table, vertical="middle"),
            title="🎬 Action Menu",
            border_style="cyan",
            height=14
        )

    def render_processing(self, processing_status: str, processing_title: str, error_message: str = None, processing_mode: str = "full") -> Panel:
        """Render the processing view."""
        table = Table.grid(padding=1)
        table.add_column(justify="left")

        # Add episode title (truncated for space)
        if processing_title:
            title_text = processing_title[:60]
            if len(processing_title) > 60:
                title_text += "..."
            table.add_row(Text(f"📻 {title_text}", style="bold white"))
            table.add_row("")  # Spacing

        # Add current status
        if processing_status == "done":
            table.add_row(Text("✅ Processing complete!", style="bold green"))
            table.add_row(Text("Returning to episode list...", style="dim italic"))
        elif processing_status == "error":
            table.add_row(Text("❌ Error occurred", style="bold red"))
            if error_message:
                error_text = error_message[:80]
                if len(error_message) > 80:
                    error_text += "..."
                table.add_row(Text(error_text, style="red dim"))
        else:
            # Show progress steps based on processing mode
            if processing_mode == "insights":
                steps = [
                    ("analyzing", "🧠 Extract insights", "magenta")
                ]
            elif processing_mode == "transcribe":
                steps = [
                    ("downloading", "📥 Download audio", "yellow"),
                    ("transcribing", "📝 Transcribe audio", "cyan"),
                ]
            else:  # full mode (default)
                steps = [
                    ("downloading", "📥 Download audio", "yellow"),
                    ("transcribing", "📝 Transcribe audio", "cyan"),
                    ("analyzing", "🧠 Extract insights", "magenta")
                ]

            for step_id, step_text, step_color in steps:
                if processing_status == step_id:
                    # Current step - animated
                    spinner = Spinner("dots", style=step_color)
                    text = Text(f" {step_text}...", style=f"bold {step_color}")
                    table.add_row(Columns([spinner, text], padding=0))
                elif self._is_step_complete(processing_status, step_id):
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

    @staticmethod
    def _is_step_complete(current_status: str, step: str) -> bool:
        """Check if a processing step is complete based on current status."""
        steps_order = ["downloading", "transcribing", "analyzing", "done"]
        if current_status in steps_order:
            current_idx = steps_order.index(current_status)
            step_idx = steps_order.index(step) if step in steps_order else -1
            return step_idx < current_idx
        return False

    def render_bulk_action_menu(self, episodes: list) -> Panel:
        """Render action menu for bulk episode selection."""
        table = Table.grid(padding=1)
        table.add_column(justify="left")

        table.add_row(Text(f"Selected: {len(episodes)} episodes", style="bold cyan"))
        table.add_row("")

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

        # Show status breakdown
        if processable:
            table.add_row(Text(f"  Ready for processing: {len(processable)}", style="green"))
        if transcribed:
            table.add_row(Text(f"  Transcribed (insights only): {len(transcribed)}", style="cyan"))
        if done:
            table.add_row(Text(f"  Already done (will skip): {len(done)}", style="dim"))

        table.add_row("")
        table.add_row(Text("Select processing mode:", style="bold"))

        # Show options based on what's selected
        if processable:
            table.add_row(Text("[1] Full processing (download + transcribe + insights)", style="white"))
            table.add_row(Text("[2] Transcribe only (download + transcribe)", style="white"))
        elif transcribed:
            # Only transcribed episodes selected
            table.add_row(Text("[1] Extract insights", style="white"))

        table.add_row("")
        table.add_row(Text("[ESC] Cancel", style="dim italic"))

        return Panel(
            Align.center(table, vertical="middle"),
            title=f"📦 Bulk Processing - {len(episodes)} episodes",
            border_style="cyan",
            height=16
        )

    def render_skip_confirmation(self, skipped: list, processing: list) -> Panel:
        """Render confirmation when some episodes will be skipped."""
        table = Table.grid(padding=1)
        table.add_column(justify="left")

        table.add_row(Text("Some episodes will be skipped:", style="bold yellow"))
        table.add_row("")

        # List skipped episodes (max 5)
        for ep in skipped[:5]:
            title = ep["title"]
            if len(title) > 40:
                title = title[:40] + "..."
            reason = "already done" if ep["status"] == "done" else f"status: {ep['status']}"
            table.add_row(Text(f"  - {title} ({reason})", style="dim"))

        if len(skipped) > 5:
            table.add_row(Text(f"  ... and {len(skipped) - 5} more", style="dim"))

        table.add_row("")
        table.add_row(Text(f"Will process: {len(processing)} episodes", style="green"))
        table.add_row(Text(f"Will skip: {len(skipped)} episodes", style="yellow"))
        table.add_row("")
        table.add_row(Text("[Enter] Continue  [ESC] Cancel", style="bold"))

        return Panel(
            Align.center(table, vertical="middle"),
            title="⚠️ Confirm Bulk Processing",
            border_style="yellow",
            height=16
        )

    def render_bulk_processing(
        self,
        current_episode: dict,
        processing_status: str,
        current_index: int,
        total_count: int,
        completed_count: int,
        processing_mode: str = "full",
        error_message: str = None
    ) -> Panel:
        """Render compact bulk processing progress."""
        table = Table.grid(padding=1)
        table.add_column(justify="left")

        # Progress header
        progress_text = f"Episode {current_index + 1}/{total_count}"
        table.add_row(Text(progress_text, style="bold cyan"))

        # Completed count
        if completed_count > 0:
            table.add_row(Text(f"Completed: {completed_count}", style="green dim"))

        table.add_row("")

        # Current episode title
        title = current_episode["title"]
        if len(title) > 50:
            title = title[:50] + "..."
        table.add_row(Text(f"Current: {title}", style="white"))
        table.add_row("")

        # Current step status
        if processing_status == "done":
            table.add_row(Text("✅ Episode complete!", style="bold green"))
        elif processing_status == "error":
            table.add_row(Text("❌ Error occurred", style="bold red"))
            if error_message:
                error_text = error_message[:60] + "..." if len(error_message) > 60 else error_message
                table.add_row(Text(error_text, style="red dim"))
        else:
            # Show current step with spinner
            step_labels = {
                "downloading": ("📥 Downloading...", "yellow"),
                "transcribing": ("📝 Transcribing...", "cyan"),
                "analyzing": ("🧠 Extracting insights...", "magenta")
            }
            if processing_status in step_labels:
                label, color = step_labels[processing_status]
                spinner = Spinner("dots", style=color)
                text = Text(f" {label}", style=f"bold {color}")
                table.add_row(Columns([spinner, text], padding=0))

        table.add_row("")
        table.add_row(Text("[Ctrl+C] Cancel remaining", style="dim italic"))

        return Panel(
            Align.center(table, vertical="middle"),
            title=f"⚡ Bulk Processing ({current_index + 1}/{total_count})",
            border_style="yellow",
            height=12
        )

    @staticmethod
    def render_quit_confirmation() -> Panel:
        """Render quit confirmation dialog."""
        return Panel(
            Text("Are you sure you want to quit?\n\n[Enter] Confirm  [ESC] Cancel", style="yellow"),
            title="⚠️ Confirm Quit",
            border_style="yellow"
        )