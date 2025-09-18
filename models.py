#!/usr/bin/env python3
"""Data models for Podcast Insights."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


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