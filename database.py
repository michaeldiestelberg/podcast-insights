#!/usr/bin/env python3
"""Database operations for Podcast Insights."""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils import iso_now


class DB:
    """Base database operations."""

    def __init__(self, base_dir: Path):
        self.db_path = base_dir / "state.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                name TEXT,
                slug TEXT,
                etag TEXT,
                last_modified TEXT,
                last_checked_at TEXT
            );
        """)
        cur.execute("""
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
        """)
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
        cur.execute("""
            INSERT OR IGNORE INTO episodes (
                feed_id, guid, audio_url, title, pub_date, episode_dir,
                audio_path, transcript_path, insights_path, status, error,
                first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', NULL, ?, ?)
        """, (
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
        ))
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


class ExtendedDB(DB):
    """Extended database operations for the interactive UI."""

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