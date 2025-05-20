"""Simple SQLite helpers for PodInsights.

This module abstracts the small SQLite database used by both the CLI and the
web interface. Each function wraps a query so callers don't need to know SQL.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional, List

DB_PATH = "episodes.db"


def init_db(db_path: str = DB_PATH) -> None:
    """Create tables if the database file is empty."""
    with sqlite3.connect(db_path) as conn:
        # Table storing RSS feeds that users have added
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                title TEXT
            )
            """
        )
        # Each processed episode is stored here along with its state
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER,
                url TEXT UNIQUE,
                title TEXT,
                transcript TEXT,
                summary TEXT,
                action_items TEXT,
                status TEXT,
                FOREIGN KEY(feed_id) REFERENCES feeds(id)
            )
            """
        )
        # Created JIRA tickets are tracked in a separate table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jira_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id INTEGER,
                action_item TEXT,
                ticket_key TEXT,
                ticket_url TEXT,
                FOREIGN KEY(episode_id) REFERENCES episodes(id)
            )
            """
        )
        # ``status`` was added later, so upgrade any existing DB on startup
        cur = conn.execute("PRAGMA table_info(episodes)")
        columns = [row[1] for row in cur.fetchall()]
        if "status" not in columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN status TEXT")
            conn.execute("UPDATE episodes SET status = 'complete'")
        conn.commit()


def get_feed(url: str, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a feed record by its RSS URL."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM feeds WHERE url = ?", (url,))
        return cur.fetchone()


def get_feed_by_id(feed_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Return a feed row given its integer ``id``."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,))
        return cur.fetchone()


def list_feeds(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return all stored feeds ordered by title."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM feeds ORDER BY title")
        return cur.fetchall()


def add_feed(url: str, title: str, db_path: str = DB_PATH) -> int:
    """Insert a new feed if needed and return its ``id``."""
    with sqlite3.connect(db_path) as conn:
        # ``INSERT OR IGNORE`` lets us call this repeatedly with the same URL
        cur = conn.execute(
            "INSERT OR IGNORE INTO feeds (url, title) VALUES (?, ?)",
            (url, title),
        )
        # ``rowcount`` will be >0 when a new row was inserted
        if cur.rowcount:
            feed_id = cur.lastrowid
        else:
            cur = conn.execute("SELECT id FROM feeds WHERE url = ?", (url,))
            feed_id = cur.fetchone()[0]
        conn.commit()
        return feed_id


def get_episode(url: str, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a processed episode by its audio URL."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM episodes WHERE url = ?", (url,))
        return cur.fetchone()


def save_episode(
    url: str,
    title: str,
    transcript: str,
    summary: str,
    action_items: Iterable[str],
    feed_id: int,
    db_path: str = DB_PATH,
) -> None:
    """Persist a fully processed episode."""
    # Store the list of action items as newline separated text
    actions = "\n".join(action_items)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO episodes
                (url, title, transcript, summary, action_items, feed_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'complete')
            """,
            (url, title, transcript, summary, actions, feed_id),
        )  # episode is complete once saved
        conn.commit()


def queue_episode(url: str, title: str, feed_id: int, db_path: str = DB_PATH) -> None:
    """Mark an episode as awaiting background processing."""
    with sqlite3.connect(db_path) as conn:
        # Insert only if we haven't seen this URL before
        conn.execute(
            """
            INSERT OR IGNORE INTO episodes (url, title, feed_id, status)
            VALUES (?, ?, ?, 'queued')
            """,
            (url, title, feed_id),
        )
        conn.commit()


def update_episode_status(url: str, status: str, db_path: str = DB_PATH) -> None:
    """Update the processing status for an episode."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE episodes SET status = ? WHERE url = ?",
            (status, url),
        )  # simple status update used by the worker thread
        conn.commit()


def list_episodes(feed_id: int, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Return all episodes belonging to a particular feed."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM episodes WHERE feed_id = ? ORDER BY id",
            (feed_id,),
        )
        return cur.fetchall()


def list_all_episodes(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """List episodes from all feeds ordered by ``id``."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM episodes ORDER BY id")
        return cur.fetchall()


def add_ticket(
    episode_id: int,
    action_item: str,
    ticket_key: str,
    ticket_url: str,
    db_path: str = DB_PATH,
) -> None:
    """Save a JIRA ticket associated with an episode."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jira_tickets (episode_id, action_item, ticket_key, ticket_url)
            VALUES (?, ?, ?, ?)
            """,
            (episode_id, action_item, ticket_key, ticket_url),
        )
        conn.commit()


def list_tickets(
    episode_id: Optional[int] | None = None, db_path: str = DB_PATH
) -> List[sqlite3.Row]:
    """List all JIRA tickets or those for a specific episode."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # Build a query that joins ticket info with episode context
        columns = (
            "jt.*, e.title AS episode_title, e.summary AS episode_summary,"
            " e.url AS episode_url, e.feed_id AS feed_id"
        )
        if episode_id is None:
            # All tickets across every episode
            cur = conn.execute(
                f"""
                SELECT {columns}
                FROM jira_tickets jt
                JOIN episodes e ON jt.episode_id = e.id
                ORDER BY jt.id
                """
            )
        else:
            # Only tickets for a specific episode
            cur = conn.execute(
                f"""
                SELECT {columns}
                FROM jira_tickets jt
                JOIN episodes e ON jt.episode_id = e.id
                WHERE jt.episode_id = ?
                ORDER BY jt.id
                """,
                (episode_id,),
            )
        return cur.fetchall()
