#!/usr/bin/env python3
"""Data models for Podcast Insights."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional


class ProcessingMode(Enum):
    """Processing modes for episode pipeline."""
    FULL = "full"           # Download + Transcribe + Insights
    TRANSCRIBE = "transcribe"  # Download + Transcribe only
    INSIGHTS = "insights"      # Insights only (requires transcript)


@dataclass
class StorageConfig:
    data_dir: Path
    temp_dir: Path


@dataclass
class RuntimeConfig:
    max_retries: int = 3
    retry_backoff_seconds: int = 5


@dataclass
class ToolsConfig:
    transcribe_cmd: str
    insights_cmd: str


@dataclass
class FeedConfig:
    url: str
    name: Optional[str] = None


@dataclass
class AppConfig:
    storage: StorageConfig
    runtime: RuntimeConfig
    tools: ToolsConfig
    feeds: List[FeedConfig]


@dataclass
class UIState:
    """State management for the TUI."""
    current_view: str = "podcast_list"
    selected_feed_id: Optional[int] = None
    selected_feed_name: Optional[str] = None
    selected_episode_id: Optional[int] = None
    episode_offset: int = 0
    episode_limit: int = 5
    processing_episode: Optional[object] = None  # sqlite3.Row
    error_message: Optional[str] = None
    processing_mode: Optional[ProcessingMode] = None
    # Bulk processing state
    bulk_episode_ids: Optional[List[int]] = None
    bulk_episodes: Optional[List[object]] = None  # List of sqlite3.Row
    bulk_current_index: int = 0
    bulk_completed_count: int = 0
    bulk_skipped_episodes: Optional[List[object]] = None