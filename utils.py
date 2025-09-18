#!/usr/bin/env python3
"""Utility functions for Podcast Insights."""

import datetime as dt
import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from models import AppConfig, FeedConfig, RuntimeConfig, StorageConfig, ToolsConfig


# Regex patterns
SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9 _\-]+")
WS_RE = re.compile(r"\s+")


def setup_logging(base_dir: Path) -> None:
    """Set up logging configuration."""
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def load_config(path: Path) -> AppConfig:
    """Load configuration from YAML file."""
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    storage = StorageConfig(
        data_dir=Path(cfg["storage"]["data_dir"]).expanduser().resolve(),
        temp_dir=Path(cfg["storage"]["temp_dir"]).expanduser().resolve(),
    )

    runtime_dict = cfg.get("runtime", {})
    runtime = RuntimeConfig(
        max_retries=int(runtime_dict.get("max_retries", 3)),
        retry_backoff_seconds=int(runtime_dict.get("retry_backoff_seconds", 5)),
    )

    tools = ToolsConfig(
        transcribe_cmd=str(cfg["tools"]["transcribe_cmd"]),
        insights_cmd=str(cfg["tools"]["insights_cmd"]),
    )

    feeds = [FeedConfig(url=f["url"], name=f.get("name")) for f in cfg.get("feeds", [])]

    return AppConfig(storage=storage, runtime=runtime, tools=tools, feeds=feeds)


def safe_name(s: str, max_len: int = 100) -> str:
    """Convert string to safe filename."""
    s = s.strip()
    s = s.replace("/", "-")
    s = SAFE_CHARS_RE.sub("", s)
    s = WS_RE.sub(" ", s).strip()
    s = s.strip(" -_")
    if not s:
        s = "untitled"
    if len(s) > max_len:
        s = s[: max_len - 8].rstrip() + "-" + short_hash(s)
    return s


def short_hash(s: str) -> str:
    """Generate short hash from string."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:6]


def iso_now() -> str:
    """Get current ISO timestamp."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def ensure_dir(p: Path) -> None:
    """Ensure directory exists."""
    p.mkdir(parents=True, exist_ok=True)


def select_enclosure(entry: Any) -> Optional[str]:
    """Select audio enclosure from feed entry."""
    # Prefer enclosures with audio/mpeg
    if getattr(entry, "enclosures", None):
        for e in entry.enclosures:
            href = getattr(e, "href", None) or e.get("href")
            typ = getattr(e, "type", None) or e.get("type")
            if href and (not typ or "audio" in typ or "mpeg" in typ):
                return href
    # Fallback to links rel=enclosure
    if getattr(entry, "links", None):
        for link in entry.links:
            if link.get("rel") == "enclosure" and link.get("href"):
                return link["href"]
    return None


def entry_guid(entry: Any) -> Optional[str]:
    """Extract GUID from feed entry."""
    for key in ("id", "guid"):
        v = getattr(entry, key, None) or entry.get(key) if isinstance(entry, dict) else None
        if v:
            return str(v)
    return None


def entry_pubdate(entry: Any) -> Optional[str]:
    """Extract publication date from feed entry."""
    for key in ("published_parsed", "updated_parsed"):
        v = getattr(entry, key, None) or (entry.get(key) if isinstance(entry, dict) else None)
        if v:
            try:
                return dt.datetime(*v[:6], tzinfo=dt.timezone.utc).date().isoformat()
            except Exception:
                pass
    return dt.date.today().isoformat()


def entry_datetime(entry: Any) -> Optional[dt.datetime]:
    """Extract datetime from feed entry."""
    for key in ("published_parsed", "updated_parsed"):
        v = getattr(entry, key, None) or (entry.get(key) if isinstance(entry, dict) else None)
        if v:
            try:
                return dt.datetime(*v[:6], tzinfo=dt.timezone.utc)
            except Exception:
                pass
    return None


def run_cmd(cmd: str, cwd: Optional[Path] = None) -> None:
    """Run shell command."""
    import subprocess

    logging.debug("Running command: %s", cmd)
    # Use shell to support quoted placeholders in config
    proc = subprocess.run(cmd, shell=True, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {cmd}")