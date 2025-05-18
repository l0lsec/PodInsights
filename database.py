"""Simple SQLite helpers for PodInsights."""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional, List

DB_PATH = "episodes.db"


def init_db(db_path: str = DB_PATH) -> None:
    """Initialize the database if it does not exist."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                title TEXT
            )
            """
        )
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
        # Ensure the episodes table has a status column for older DBs
        cur = conn.execute("PRAGMA table_info(episodes)")
        columns = [row[1] for row in cur.fetchall()]
        if "status" not in columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN status TEXT")
            conn.execute("UPDATE episodes SET status = 'complete'")
        conn.commit()


def get_feed(url: str, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a feed by URL."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM feeds WHERE url = ?", (url,))
        return cur.fetchone()


def get_feed_by_id(feed_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,))
        return cur.fetchone()


def list_feeds(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM feeds ORDER BY title")
        return cur.fetchall()


def add_feed(url: str, title: str, db_path: str = DB_PATH) -> int:
    """Insert or return an existing feed and return its id."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO feeds (url, title) VALUES (?, ?)", (url, title)
        )
        if cur.rowcount:
            feed_id = cur.lastrowid
        else:
            cur = conn.execute("SELECT id FROM feeds WHERE url = ?", (url,))
            feed_id = cur.fetchone()[0]
        conn.commit()
        return feed_id


def get_episode(url: str, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a processed episode by URL."""
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
    """Save a processed episode to the DB."""
    actions = "\n".join(action_items)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO episodes
                (url, title, transcript, summary, action_items, feed_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'complete')
            """,
            (url, title, transcript, summary, actions, feed_id),
        )
        conn.commit()


def queue_episode(url: str, title: str, feed_id: int, db_path: str = DB_PATH) -> None:
    """Queue an episode for processing."""
    with sqlite3.connect(db_path) as conn:
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
        )
        conn.commit()


def list_episodes(feed_id: int, db_path: str = DB_PATH) -> List[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM episodes WHERE feed_id = ? ORDER BY id", (feed_id,)
        )
        return cur.fetchall()


def list_all_episodes(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """List episodes from all feeds ordered by id."""
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
        columns = (
            "jt.*, e.title AS episode_title, e.summary AS episode_summary,"
            " e.url AS episode_url, e.feed_id AS feed_id"
        )
        if episode_id is None:
            cur = conn.execute(
                f"""
                SELECT {columns}
                FROM jira_tickets jt
                JOIN episodes e ON jt.episode_id = e.id
                ORDER BY jt.id
                """
            )
        else:
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
