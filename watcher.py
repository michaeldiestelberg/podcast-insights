#!/usr/bin/env python3
import argparse
import contextlib
import datetime as dt
import hashlib
import logging
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import feedparser  # type: ignore
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
    poll_interval_minutes: int = 30
    max_retries: int = 3
    retry_backoff_seconds: int = 5
    sequential: bool = True
    max_new_per_feed: Optional[int] = 1


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
        poll_interval_minutes=int(runtime_dict.get("poll_interval_minutes", 30)),
        max_retries=int(runtime_dict.get("max_retries", 3)),
        retry_backoff_seconds=int(runtime_dict.get("retry_backoff_seconds", 5)),
        sequential=bool(runtime_dict.get("sequential", True)),
        max_new_per_feed=runtime_dict.get("max_new_per_feed"),
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

    def iter_new_or_incomplete(self, feed_id: int, max_new: Optional[int] = None) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        # Process new and incomplete in order: new -> downloading -> downloaded -> transcribing -> transcribed -> analyzing
        cur.execute(
            """
            SELECT * FROM episodes
            WHERE feed_id = ? AND status IN ('new','downloading','downloaded','transcribing','transcribed','analyzing','error_download','error_transcribe','error_insights')
            ORDER BY first_seen_at ASC
            """,
            (feed_id,),
        )
        rows = cur.fetchall()
        if max_new is None:
            return list(rows)
        # Cap how many episodes we start from 'new' status for this cycle
        selected = []
        new_count = 0
        for r in rows:
            if r["status"] == "new":
                if new_count >= max_new:
                    continue
                new_count += 1
            selected.append(r)
        return selected


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
        self._stop = False

    def stop(self, *args: Any) -> None:
        logging.info("Received stop signal; will stop after current step.")
        self._stop = True

    def run_loop(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        while not self._stop:
            self.poll_once()
            if self._stop:
                break
            interval = self.cfg.runtime.poll_interval_minutes
            logging.info("Sleeping %d minutes before next poll...", interval)
            for _ in range(interval * 60):
                if self._stop:
                    break
                time.sleep(1)

    def poll_once(self) -> None:
        for feed_cfg in self.cfg.feeds:
            if self._stop:
                break
            try:
                self._process_feed(feed_cfg)
            except Exception as e:
                logging.exception("Error processing feed %s: %s", feed_cfg.url, e)

    def _process_feed(self, feed_cfg: FeedConfig) -> None:
        logging.info("Polling feed: %s", feed_cfg.url)

        # Fetch with conditional headers via feedparser
        etag, last_mod = self.db.fetch_feed_http_cache(feed_cfg.url)
        parsed = feedparser.parse(feed_cfg.url, etag=etag, modified=last_mod)  # network call

        # Handle 304 Not Modified: still process any pending episodes
        status_code = getattr(parsed, "status", None)
        if status_code == 304:
            logging.info("No changes for %s (304)", feed_cfg.url)
            feed_id = self.db.upsert_feed(feed_cfg.url, feed_cfg.name, None)
            episodes = self.db.iter_new_or_incomplete(feed_id, self.cfg.runtime.max_new_per_feed)
            for ep in episodes:
                if self._stop:
                    break
                self._process_episode(ep)
            return

        # Extract feed title and set slug
        feed_title = (
            feed_cfg.name
            or getattr(parsed.feed, "title", None)
            or feed_cfg.url
        )
        feed_slug = safe_name(feed_title)
        feed_id = self.db.upsert_feed(feed_cfg.url, feed_title, feed_slug)

        # Update ETag and Last-Modified
        new_etag = getattr(parsed, "etag", None)
        new_modified = getattr(parsed, "modified", None)
        self.db.update_feed_http(feed_id, new_etag, new_modified)

        # Build base dir for this feed
        feed_dir = self.cfg.storage.data_dir / feed_slug
        ensure_dir(feed_dir)

        # Iterate entries
        entries = list(getattr(parsed, "entries", []))
        if not entries:
            logging.info("No entries in feed: %s", feed_cfg.url)
            return

        # Newest-first ordering for one-shot and typical runs
        try:
            entries.sort(
                key=lambda e: entry_datetime(e) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                reverse=True,
            )
        except Exception:
            # If any sorting error occurs, keep feed order (usually newest-first)
            pass

        added_count = 0
        for entry in entries:
            if self._stop:
                break
            guid = entry_guid(entry)
            audio = select_enclosure(entry)
            title = getattr(entry, "title", None) or "Untitled Episode"
            pubdate = entry_pubdate(entry)

            if not audio:
                logging.debug("Skipping entry without audio: %s", title)
                continue

            # Skip if already known
            existing = self.db.find_episode(feed_id, guid, audio)
            if existing:
                continue

            # Cap how many to add this cycle
            max_new = self.cfg.runtime.max_new_per_feed
            if max_new is not None and added_count >= max_new:
                continue

            # Compute episode dir and paths
            episode_dir_name = f"{pubdate}_{safe_name(title)}"
            episode_dir = feed_dir / episode_dir_name
            audio_file = f"{safe_name(title)}.mp3"
            transcript_file = f"{safe_name(title)}.transcript.md"
            insights_file = f"{safe_name(title)}.insights.md"

            audio_path = episode_dir / audio_file
            transcript_path = episode_dir / transcript_file
            insights_path = episode_dir / insights_file

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
            added_count += 1

        # Process sequentially any new/incomplete episodes
        episodes = self.db.iter_new_or_incomplete(feed_id, self.cfg.runtime.max_new_per_feed)
        for ep in episodes:
            if self._stop:
                break
            self._process_episode(ep)

    def _process_episode(self, ep: sqlite3.Row) -> None:
        episode_id = int(ep["id"])
        # Always reload the latest DB row to avoid stale status within a single pass
        row = self.db.get_episode_by_id(episode_id)
        episode_dir = Path(row["episode_dir"])
        audio_path = Path(row["audio_path"])
        transcript_path = Path(row["transcript_path"])
        insights_path = Path(row["insights_path"])

        ensure_dir(episode_dir)

        # Download stage
        if not audio_path.exists():
            self.db.update_episode_status(episode_id, "downloading")
            self._download_audio(row["audio_url"], audio_path)
        self.db.update_episode_status(episode_id, "downloaded")

        # Transcription stage
        if not transcript_path.exists():
            self.db.update_episode_status(episode_id, "transcribing")
            self._run_transcription(audio_path, transcript_path)
            if not transcript_path.exists():
                raise RuntimeError(f"Transcription did not produce expected file: {transcript_path}")
        self.db.update_episode_status(episode_id, "transcribed")

        # Insights stage
        if not insights_path.exists():
            self.db.update_episode_status(episode_id, "analyzing")
            self._run_insights(transcript_path, insights_path)
            if not insights_path.exists():
                raise RuntimeError(f"Insights extraction did not produce expected file: {insights_path}")
        self.db.update_episode_status(episode_id, "done")

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


# --------------- CLI ---------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Podcast Insights pipeline")
    p.add_argument("command", choices=["run", "poll-once", "status"], help="Command to run")
    p.add_argument("--config", default="config.yaml", help="Path to YAML config")
    return p.parse_args()


def print_status(db: DB) -> None:
    cur = db.conn.cursor()
    cur.execute("SELECT COUNT(*) FROM feeds")
    feeds = cur.fetchone()[0]
    cur.execute("SELECT status, COUNT(*) as c FROM episodes GROUP BY status ORDER BY status")
    eps = cur.fetchall()
    logging.info("Feeds: %d", feeds)
    for r in eps:
        logging.info("%s: %d", r["status"], r["c"])


def main() -> None:
    args = parse_args()
    cfg = load_config(Path(args.config))
    setup_logging(Path.cwd())
    watcher = Watcher(cfg)
    if args.command == "run":
        watcher.run_loop()
    elif args.command == "poll-once":
        watcher.poll_once()
    elif args.command == "status":
        print_status(watcher.db)


if __name__ == "__main__":
    main()
