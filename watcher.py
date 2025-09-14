#!/usr/bin/env python3
import contextlib
import datetime as dt
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml


# --------------- Logging ---------------

def setup_logging(base_dir: Path) -> None:
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


# --------------- Config ---------------

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


def load_config(path: Path) -> AppConfig:
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


# --------------- DB ---------------

class DB:
    def __init__(self, base_dir: Path):
        self.db_path = base_dir / "state.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            PRAGMA journal_mode=WAL;
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                name TEXT,
                slug TEXT,
                etag TEXT,
                last_modified TEXT,
                last_checked_at TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY,
                feed_id INTEGER NOT NULL,
                guid TEXT,
                audio_url TEXT,
                title TEXT,
                pub_date TEXT,
                episode_dir TEXT,
                audio_path TEXT,
                transcript_path TEXT,
                insights_path TEXT,
                status TEXT NOT NULL,
                error TEXT,
                first_seen_at TEXT,
                updated_at TEXT,
                UNIQUE(feed_id, guid),
                UNIQUE(feed_id, audio_url),
                FOREIGN KEY(feed_id) REFERENCES feeds(id)
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_episodes_feed_status ON episodes(feed_id, status);")
        self.conn.commit()

    # Feed helpers
    def upsert_feed(self, url: str, name: Optional[str], slug: Optional[str]) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT id, name, slug FROM feeds WHERE url = ?", (url,))
        row = cur.fetchone()
        if row:
            # Only update name/slug if not set
            new_name = row["name"] or name
            new_slug = row["slug"] or slug
            cur.execute("UPDATE feeds SET name = ?, slug = ? WHERE id = ?", (new_name, new_slug, row["id"]))
            self.conn.commit()
            return int(row["id"])
        cur.execute(
            "INSERT INTO feeds(url, name, slug, last_checked_at) VALUES (?, ?, ?, ?)",
            (url, name, slug, iso_now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_feed_meta(self, feed_id: int) -> sqlite3.Row:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,))
        return cur.fetchone()

    def update_feed_http(self, feed_id: int, etag: Optional[str], last_modified: Optional[str]) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE feeds SET etag = ?, last_modified = ?, last_checked_at = ? WHERE id = ?",
            (etag, last_modified, iso_now(), feed_id),
        )
        self.conn.commit()

    def fetch_feed_http_cache(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        cur = self.conn.cursor()
        cur.execute("SELECT etag, last_modified FROM feeds WHERE url = ?", (url,))
        row = cur.fetchone()
        if row:
            return row["etag"], row["last_modified"]
        return None, None

    # Episode helpers
    def find_episode(self, feed_id: int, guid: Optional[str], audio_url: Optional[str]) -> Optional[sqlite3.Row]:
        cur = self.conn.cursor()
        if guid:
            cur.execute("SELECT * FROM episodes WHERE feed_id = ? AND guid = ?", (feed_id, guid))
            row = cur.fetchone()
            if row:
                return row
        if audio_url:
            cur.execute("SELECT * FROM episodes WHERE feed_id = ? AND audio_url = ?", (feed_id, audio_url))
            return cur.fetchone()
        return None

    def insert_episode(
        self,
        feed_id: int,
        guid: Optional[str],
        audio_url: Optional[str],
        title: str,
        pub_date: Optional[str],
        episode_dir: Path,
        audio_path: Path,
        transcript_path: Path,
        insights_path: Path,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO episodes (
                feed_id, guid, audio_url, title, pub_date, episode_dir,
                audio_path, transcript_path, insights_path, status, error,
                first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', NULL, ?, ?)
            """,
            (
                feed_id,
                guid,
                audio_url,
                title,
                pub_date,
                str(episode_dir),
                str(audio_path),
                str(transcript_path),
                str(insights_path),
                iso_now(),
                iso_now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_episode_status(self, episode_id: int, status: str, error: Optional[str] = None) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE episodes SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, iso_now(), episode_id),
        )
        self.conn.commit()

    def get_episode_by_id(self, episode_id: int) -> sqlite3.Row:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Episode not found: {episode_id}")
        return row



# --------------- Helpers ---------------

SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9 _\-]+")
WS_RE = re.compile(r"\s+")


def safe_name(s: str, max_len: int = 100) -> str:
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
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:6]


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def select_enclosure(entry: Any) -> Optional[str]:
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
    for key in ("id", "guid"):
        v = getattr(entry, key, None) or entry.get(key) if isinstance(entry, dict) else None
        if v:
            return str(v)
    return None


def entry_pubdate(entry: Any) -> Optional[str]:
    for key in ("published_parsed", "updated_parsed"):
        v = getattr(entry, key, None) or (entry.get(key) if isinstance(entry, dict) else None)
        if v:
            try:
                return dt.datetime(*v[:6], tzinfo=dt.timezone.utc).date().isoformat()
            except Exception:
                pass
    return dt.date.today().isoformat()


def entry_datetime(entry: Any) -> Optional[dt.datetime]:
    for key in ("published_parsed", "updated_parsed"):
        v = getattr(entry, key, None) or (entry.get(key) if isinstance(entry, dict) else None)
        if v:
            try:
                return dt.datetime(*v[:6], tzinfo=dt.timezone.utc)
            except Exception:
                pass
    return None


# --------------- Core ---------------

class Watcher:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        ensure_dir(self.cfg.storage.data_dir)
        ensure_dir(self.cfg.storage.temp_dir)
        self.db = DB(Path.cwd())


    # --------------- Actions ---------------
    def _download_audio(self, url: str, dest: Path) -> None:
        logging.info("Downloading audio: %s", url)
        ensure_dir(dest.parent)
        ensure_dir(self.cfg.storage.temp_dir)

        temp_path = self.cfg.storage.temp_dir / (dest.name + ".part")
        # Clean pre-existing partial file
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()

        session = requests.Session()
        headers = {"User-Agent": "PodcastInsights/1.0 (+https://localhost)"}
        retries = self.cfg.runtime.max_retries
        backoff = self.cfg.runtime.retry_backoff_seconds
        last_err: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                with session.get(url, headers=headers, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("Content-Length", "0"))
                    written = 0
                    with open(temp_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                                written += len(chunk)
                    if total and written != total:
                        raise IOError(f"Size mismatch: expected {total}, wrote {written}")
                # Atomic move
                shutil.move(str(temp_path), str(dest))
                logging.info("Downloaded to %s", dest)
                return
            except Exception as e:
                last_err = e
                logging.warning("Download failed (attempt %d/%d): %s", attempt, retries, e)
                time.sleep(backoff * attempt)
        raise RuntimeError(f"Failed to download after {retries} attempts: {last_err}")

    def _run_transcription(self, audio_path: Path, transcript_path: Path) -> None:
        cmd = self.cfg.tools.transcribe_cmd.format(
            audio=str(audio_path), transcript=str(transcript_path)
        )
        logging.info("Transcribing: %s", audio_path)
        run_cmd(cmd, cwd=None)
        logging.info("Transcription done: %s", transcript_path)

    def _run_insights(self, transcript_path: Path, insights_path: Path) -> None:
        episode_dir = str(insights_path.parent)
        insights_file = insights_path.name
        cmd = self.cfg.tools.insights_cmd.format(
            transcript=str(transcript_path), episode_dir=episode_dir, insights_file=insights_file
        )
        logging.info("Extracting insights: %s", insights_path)
        run_cmd(cmd, cwd=None)
        logging.info("Insights extraction done: %s", insights_path)


def run_cmd(cmd: str, cwd: Optional[Path]) -> None:
    logging.debug("Running command: %s", cmd)
    # Use shell to support quoted placeholders in config
    proc = subprocess.run(cmd, shell=True, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {cmd}")
