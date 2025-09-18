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
            f"\n{showing_text}\n[Number+Enter] Select episode  [l] Load more  [ESC] Back  [q] Quit",
            style="dim"
        )

        return Panel(
            Group(table, help_text),
            title=f"🎧 {feed_name}",
            border_style="cyan"
        )

    def render_processing(self, processing_status: str, processing_title: str, error_message: str = None) -> Panel:
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
            # Show progress steps
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

    @staticmethod
    def render_quit_confirmation() -> Panel:
        """Render quit confirmation dialog."""
        return Panel(
            Text("Are you sure you want to quit?\n\n[Enter] Confirm  [ESC] Cancel", style="yellow"),
            title="⚠️ Confirm Quit",
            border_style="yellow"
        )