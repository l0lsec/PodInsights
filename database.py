"""Simple SQLite helpers for PodInsights.

This module abstracts the small SQLite database used by both the CLI and the
web interface. Each function wraps a query so callers don't need to know SQL.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional, List
from datetime import datetime

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
                title TEXT,
                feed_type TEXT,
                last_post TEXT,
                item_count INTEGER,
                last_checked TEXT
            )
            """
        )
        # Upgrade existing feeds table with new columns
        cur = conn.execute("PRAGMA table_info(feeds)")
        columns = [row[1] for row in cur.fetchall()]
        if "feed_type" not in columns:
            conn.execute("ALTER TABLE feeds ADD COLUMN feed_type TEXT")
        if "last_post" not in columns:
            conn.execute("ALTER TABLE feeds ADD COLUMN last_post TEXT")
        if "item_count" not in columns:
            conn.execute("ALTER TABLE feeds ADD COLUMN item_count INTEGER")
        if "last_checked" not in columns:
            conn.execute("ALTER TABLE feeds ADD COLUMN last_checked TEXT")
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
                published TEXT,
                processed_at TEXT,
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
        # Generated articles are stored here
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id INTEGER,
                topic TEXT,
                style TEXT,
                content TEXT,
                created_at TEXT,
                FOREIGN KEY(episode_id) REFERENCES episodes(id)
            )
            """
        )
        # Social media posts generated for articles
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER,
                platform TEXT,
                content TEXT,
                created_at TEXT,
                used INTEGER DEFAULT 0,
                FOREIGN KEY(article_id) REFERENCES articles(id)
            )
            """
        )
        # Upgrade any existing DB with newer columns
        cur = conn.execute("PRAGMA table_info(episodes)")
        columns = [row[1] for row in cur.fetchall()]
        if "status" not in columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN status TEXT")
            conn.execute("UPDATE episodes SET status = 'complete'")
        if "published" not in columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN published TEXT")
        if "processed_at" not in columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN processed_at TEXT")
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


def delete_feed(feed_id: int, db_path: str = DB_PATH) -> None:
    """Delete a feed and all associated episodes, articles, and tickets."""
    with sqlite3.connect(db_path) as conn:
        # Delete tickets for episodes in this feed
        conn.execute(
            """
            DELETE FROM jira_tickets WHERE episode_id IN (
                SELECT id FROM episodes WHERE feed_id = ?
            )
            """,
            (feed_id,),
        )
        # Delete articles for episodes in this feed
        conn.execute(
            """
            DELETE FROM articles WHERE episode_id IN (
                SELECT id FROM episodes WHERE feed_id = ?
            )
            """,
            (feed_id,),
        )
        # Delete episodes for this feed
        conn.execute("DELETE FROM episodes WHERE feed_id = ?", (feed_id,))
        # Delete the feed itself
        conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        conn.commit()


def delete_feeds_bulk(feed_ids: List[int], db_path: str = DB_PATH) -> int:
    """Delete multiple feeds and all their associated data. Returns count deleted."""
    if not feed_ids:
        return 0
    placeholders = ",".join("?" * len(feed_ids))
    with sqlite3.connect(db_path) as conn:
        # Delete tickets for episodes in these feeds
        conn.execute(
            f"""
            DELETE FROM jira_tickets WHERE episode_id IN (
                SELECT id FROM episodes WHERE feed_id IN ({placeholders})
            )
            """,
            feed_ids,
        )
        # Delete articles for episodes in these feeds
        conn.execute(
            f"""
            DELETE FROM articles WHERE episode_id IN (
                SELECT id FROM episodes WHERE feed_id IN ({placeholders})
            )
            """,
            feed_ids,
        )
        # Delete episodes for these feeds
        conn.execute(
            f"DELETE FROM episodes WHERE feed_id IN ({placeholders})",
            feed_ids,
        )
        # Delete the feeds themselves
        cur = conn.execute(
            f"DELETE FROM feeds WHERE id IN ({placeholders})",
            feed_ids,
        )
        conn.commit()
        return cur.rowcount


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


def update_feed_metadata(
    feed_id: int,
    feed_type: str,
    last_post: str | None,
    item_count: int,
    db_path: str = DB_PATH,
) -> None:
    """Update cached metadata for a feed."""
    from datetime import datetime
    last_checked = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE feeds 
            SET feed_type = ?, last_post = ?, item_count = ?, last_checked = ?
            WHERE id = ?
            """,
            (feed_type, last_post, item_count, last_checked, feed_id),
        )
        conn.commit()


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
    published: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Persist a fully processed episode."""
    # Store the list of action items as newline separated text
    actions = "\n".join(action_items)
    processed_at = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO episodes
                (url, title, transcript, summary, action_items, feed_id, status, published, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, 'complete', ?, ?)
            """,
            (url, title, transcript, summary, actions, feed_id, published, processed_at),
        )  # episode is complete once saved
        conn.commit()


def queue_episode(
    url: str,
    title: str,
    feed_id: int,
    published: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Mark an episode as awaiting background processing."""
    with sqlite3.connect(db_path) as conn:
        # Insert only if we haven't seen this URL before
        conn.execute(
            """
            INSERT OR IGNORE INTO episodes (url, title, feed_id, status, published)
            VALUES (?, ?, ?, 'queued', ?)
            """,
            (url, title, feed_id, published),
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


def get_episode_by_id(episode_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve an episode by its database ID."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        return cur.fetchone()


def delete_episode_by_id(episode_id: int, db_path: str = DB_PATH) -> None:
    """Delete an episode and all its associated articles and tickets."""
    with sqlite3.connect(db_path) as conn:
        # Delete associated tickets
        conn.execute("DELETE FROM jira_tickets WHERE episode_id = ?", (episode_id,))
        # Delete associated articles
        conn.execute("DELETE FROM articles WHERE episode_id = ?", (episode_id,))
        # Delete the episode
        conn.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
        conn.commit()


def delete_episodes_bulk(episode_ids: List[int], db_path: str = DB_PATH) -> int:
    """Delete multiple episodes and all their associated data. Returns count deleted."""
    if not episode_ids:
        return 0
    placeholders = ",".join("?" * len(episode_ids))
    with sqlite3.connect(db_path) as conn:
        # Delete associated tickets
        conn.execute(
            f"DELETE FROM jira_tickets WHERE episode_id IN ({placeholders})",
            episode_ids,
        )
        # Delete associated articles
        conn.execute(
            f"DELETE FROM articles WHERE episode_id IN ({placeholders})",
            episode_ids,
        )
        # Delete the episodes
        cur = conn.execute(
            f"DELETE FROM episodes WHERE id IN ({placeholders})",
            episode_ids,
        )
        conn.commit()
        return cur.rowcount


def reset_episode_for_reprocess(episode_id: int, db_path: str = DB_PATH) -> None:
    """Clear episode data to prepare for reprocessing."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE episodes 
            SET transcript = NULL, summary = NULL, action_items = NULL, 
                status = 'queued', processed_at = NULL
            WHERE id = ?
            """,
            (episode_id,),
        )
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


def list_all_episodes(order_by: str = "id", db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """List episodes from all feeds ordered by the provided column."""
    valid = {"id", "published", "processed_at"}
    column = order_by if order_by in valid else "id"
    direction = "DESC" if column in {"published", "processed_at"} else "ASC"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(f"SELECT * FROM episodes ORDER BY {column} {direction}")
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
            " e.url AS episode_url, e.feed_id AS feed_id, e.published AS published"
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


def add_article(
    episode_id: int,
    topic: str,
    style: str,
    content: str,
    db_path: str = DB_PATH,
) -> int:
    """Save a generated article and return its id."""
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO articles (episode_id, topic, style, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (episode_id, topic, style, content, created_at),
        )
        conn.commit()
        return cur.lastrowid


def get_article(article_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a single article by its id."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT a.*, e.title AS episode_title, e.url AS episode_url, e.feed_id,
                   f.title AS podcast_title, f.url AS podcast_url
            FROM articles a
            JOIN episodes e ON a.episode_id = e.id
            LEFT JOIN feeds f ON e.feed_id = f.id
            WHERE a.id = ?
            """,
            (article_id,),
        )
        return cur.fetchone()


def update_article(
    article_id: int,
    topic: str | None = None,
    style: str | None = None,
    content: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Update an existing article's fields."""
    with sqlite3.connect(db_path) as conn:
        # Build update query dynamically based on provided fields
        updates = []
        params = []
        if topic is not None:
            updates.append("topic = ?")
            params.append(topic)
        if style is not None:
            updates.append("style = ?")
            params.append(style)
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        
        if updates:
            params.append(article_id)
            conn.execute(
                f"UPDATE articles SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()


def delete_article(article_id: int, db_path: str = DB_PATH) -> None:
    """Delete an article and its social posts by its id."""
    with sqlite3.connect(db_path) as conn:
        # Delete associated social posts first
        conn.execute("DELETE FROM social_posts WHERE article_id = ?", (article_id,))
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        conn.commit()


def list_articles(
    episode_id: Optional[int] = None, db_path: str = DB_PATH
) -> List[sqlite3.Row]:
    """List articles, optionally filtered by episode."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if episode_id is None:
            cur = conn.execute(
                """
                SELECT a.*, e.title AS episode_title, e.url AS episode_url, e.feed_id,
                       f.title AS podcast_title
                FROM articles a
                JOIN episodes e ON a.episode_id = e.id
                LEFT JOIN feeds f ON e.feed_id = f.id
                ORDER BY a.created_at DESC
                """
            )
        else:
            cur = conn.execute(
                """
                SELECT a.*, e.title AS episode_title, e.url AS episode_url, e.feed_id,
                       f.title AS podcast_title
                FROM articles a
                JOIN episodes e ON a.episode_id = e.id
                LEFT JOIN feeds f ON e.feed_id = f.id
                WHERE a.episode_id = ?
                ORDER BY a.created_at DESC
                """,
                (episode_id,),
            )
        return cur.fetchall()


# --- Social Posts Functions ---


def add_social_post(
    article_id: int,
    platform: str,
    content: str,
    db_path: str = DB_PATH,
) -> int:
    """Save a generated social media post and return its id."""
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO social_posts (article_id, platform, content, created_at, used)
            VALUES (?, ?, ?, ?, 0)
            """,
            (article_id, platform, content, created_at),
        )
        conn.commit()
        return cur.lastrowid


def list_social_posts(
    article_id: Optional[int] = None, db_path: str = DB_PATH
) -> List[sqlite3.Row]:
    """List social posts, optionally filtered by article."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if article_id is None:
            cur = conn.execute(
                """
                SELECT sp.*, a.topic AS article_topic
                FROM social_posts sp
                JOIN articles a ON sp.article_id = a.id
                ORDER BY sp.created_at DESC
                """
            )
        else:
            cur = conn.execute(
                """
                SELECT sp.*, a.topic AS article_topic
                FROM social_posts sp
                JOIN articles a ON sp.article_id = a.id
                WHERE sp.article_id = ?
                ORDER BY sp.platform, sp.id
                """,
                (article_id,),
            )
        return cur.fetchall()


def get_social_post(post_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Retrieve a single social post by its id."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM social_posts WHERE id = ?", (post_id,))
        return cur.fetchone()


def delete_social_post(post_id: int, db_path: str = DB_PATH) -> None:
    """Delete a social post by its id."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM social_posts WHERE id = ?", (post_id,))
        conn.commit()


def delete_social_posts_bulk(post_ids: List[int], db_path: str = DB_PATH) -> int:
    """Delete multiple social posts. Returns count deleted."""
    if not post_ids:
        return 0
    placeholders = ",".join("?" * len(post_ids))
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"DELETE FROM social_posts WHERE id IN ({placeholders})",
            post_ids,
        )
        conn.commit()
        return cur.rowcount


def delete_social_posts_for_article(article_id: int, db_path: str = DB_PATH) -> int:
    """Delete all social posts for an article. Returns count deleted."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM social_posts WHERE article_id = ?",
            (article_id,),
        )
        conn.commit()
        return cur.rowcount


def mark_social_post_used(post_id: int, used: bool = True, db_path: str = DB_PATH) -> None:
    """Mark a social post as used or unused."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE social_posts SET used = ? WHERE id = ?",
            (1 if used else 0, post_id),
        )
        conn.commit()


def update_social_post(post_id: int, content: str, db_path: str = DB_PATH) -> None:
    """Update the content of a social post."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE social_posts SET content = ? WHERE id = ?",
            (content, post_id),
        )
        conn.commit()
