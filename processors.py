#!/usr/bin/env python3
"""Processing pipeline for Podcast Insights."""

import contextlib
import datetime as dt
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

import feedparser
import requests

from database import ExtendedDB
from models import AppConfig
from utils import (
    ensure_dir,
    entry_datetime,
    entry_guid,
    entry_pubdate,
    iso_now,
    safe_name,
    select_enclosure,
)


class BaseProcessor:
    """Base class for processors."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        ensure_dir(self.cfg.storage.data_dir)
        ensure_dir(self.cfg.storage.temp_dir)
        self.db = ExtendedDB(Path.cwd())


class FeedProcessor(BaseProcessor):
    """Process RSS feeds and populate episodes."""

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

                # Process all entries
                entries = list(getattr(parsed, "entries", []))
                if not entries:
                    continue

                # Sort newest first
                try:
                    entries.sort(
                        key=lambda e: entry_datetime(e) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                        reverse=True
                    )
                except Exception:
                    pass

                # Add ALL episodes to database
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

                    # Insert episode as 'new'
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


class EpisodeProcessor(BaseProcessor):
    """Process individual episodes."""

    def __init__(self, cfg: AppConfig, status_callback=None):
        super().__init__(cfg)
        self.status_callback = status_callback

    def process_single_episode(self, episode_id: int) -> Tuple[bool, Optional[str]]:
        """Process a single episode through the full pipeline."""
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

    def _download_audio(self, url: str, dest: Path) -> None:
        """Download audio file."""
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
        """Run transcription on audio file (suppressing output in interactive mode)."""
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
        """Run insights extraction on transcript (suppressing output in interactive mode)."""
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