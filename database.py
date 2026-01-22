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
        # LinkedIn OAuth tokens storage
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS linkedin_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                access_token TEXT,
                refresh_token TEXT,
                expires_at TEXT,
                member_id TEXT,
                user_urn TEXT,
                display_name TEXT,
                email TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        # Scheduled posts queue for LinkedIn and other platforms
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                social_post_id INTEGER,
                article_id INTEGER,
                post_type TEXT,
                platform TEXT DEFAULT 'linkedin',
                scheduled_for TEXT,
                status TEXT DEFAULT 'pending',
                linkedin_post_urn TEXT,
                error_message TEXT,
                created_at TEXT,
                posted_at TEXT,
                FOREIGN KEY(social_post_id) REFERENCES social_posts(id),
                FOREIGN KEY(article_id) REFERENCES articles(id)
            )
            """
        )
        # Schedule settings for configurable time slots
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE,
                setting_value TEXT,
                updated_at TEXT
            )
            """
        )
        # Time slots for queue-based scheduling
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule_time_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_of_week INTEGER,
                time_slot TEXT,
                enabled INTEGER DEFAULT 1,
                created_at TEXT
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
    """Retrieve a single social post by its id, including the article topic."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT sp.*, a.topic AS article_topic
            FROM social_posts sp
            LEFT JOIN articles a ON sp.article_id = a.id
            WHERE sp.id = ?
            """,
            (post_id,),
        )
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


# --- LinkedIn Token Functions ---


def save_linkedin_token(
    access_token: str,
    expires_at: str,
    member_id: str,
    user_urn: str,
    display_name: str | None = None,
    email: str | None = None,
    refresh_token: str | None = None,
    db_path: str = DB_PATH,
) -> int:
    """Save or update LinkedIn OAuth tokens. Returns the token record id."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        # Check if we already have a token (single user mode)
        cur = conn.execute("SELECT id FROM linkedin_tokens LIMIT 1")
        existing = cur.fetchone()

        if existing:
            # Update existing token
            conn.execute(
                """
                UPDATE linkedin_tokens SET
                    access_token = ?,
                    refresh_token = ?,
                    expires_at = ?,
                    member_id = ?,
                    user_urn = ?,
                    display_name = ?,
                    email = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    access_token,
                    refresh_token,
                    expires_at,
                    member_id,
                    user_urn,
                    display_name,
                    email,
                    now,
                    existing[0],
                ),
            )
            conn.commit()
            return existing[0]
        else:
            # Insert new token
            cur = conn.execute(
                """
                INSERT INTO linkedin_tokens
                    (access_token, refresh_token, expires_at, member_id, user_urn,
                     display_name, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    access_token,
                    refresh_token,
                    expires_at,
                    member_id,
                    user_urn,
                    display_name,
                    email,
                    now,
                    now,
                ),
            )
            conn.commit()
            return cur.lastrowid


def get_linkedin_token(db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Get the stored LinkedIn token (single user mode)."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM linkedin_tokens LIMIT 1")
        return cur.fetchone()


def delete_linkedin_token(db_path: str = DB_PATH) -> None:
    """Delete all LinkedIn tokens (disconnect)."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM linkedin_tokens")
        conn.commit()


def update_linkedin_token(
    access_token: str,
    expires_at: str,
    refresh_token: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Update the access token after a refresh."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        if refresh_token:
            conn.execute(
                """
                UPDATE linkedin_tokens SET
                    access_token = ?,
                    refresh_token = ?,
                    expires_at = ?,
                    updated_at = ?
                """,
                (access_token, refresh_token, expires_at, now),
            )
        else:
            conn.execute(
                """
                UPDATE linkedin_tokens SET
                    access_token = ?,
                    expires_at = ?,
                    updated_at = ?
                """,
                (access_token, expires_at, now),
            )
        conn.commit()


def update_linkedin_member_urn(
    member_id: str,
    user_urn: str | None = None,
    display_name: str | None = None,
    db_path: str = DB_PATH,
) -> bool:
    """Manually update the member ID and URN for LinkedIn posting.
    
    This is useful when the user only has w_member_social scope
    and profile endpoints don't work.
    
    Returns True if updated successfully.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    if user_urn is None:
        user_urn = f"urn:li:person:{member_id}"
    
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("SELECT id FROM linkedin_tokens LIMIT 1")
        existing = cur.fetchone()
        if not existing:
            return False
        
        conn.execute(
            """
            UPDATE linkedin_tokens SET
                member_id = ?,
                user_urn = ?,
                display_name = COALESCE(?, display_name),
                updated_at = ?
            WHERE id = ?
            """,
            (member_id, user_urn, display_name, now, existing[0]),
        )
        conn.commit()
        return True


# --- Scheduled Posts Functions ---


def add_scheduled_post(
    scheduled_for: str,
    post_type: str,
    social_post_id: int | None = None,
    article_id: int | None = None,
    platform: str = "linkedin",
    db_path: str = DB_PATH,
) -> int:
    """Add a post to the schedule queue. Returns the scheduled post id."""
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO scheduled_posts
                (social_post_id, article_id, post_type, platform, scheduled_for,
                 status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (social_post_id, article_id, post_type, platform, scheduled_for, created_at),
        )
        conn.commit()
        return cur.lastrowid


def get_scheduled_post(scheduled_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    """Get a single scheduled post by id."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM scheduled_posts WHERE id = ?", (scheduled_id,))
        return cur.fetchone()


def list_scheduled_posts(
    status: str | None = None,
    platform: str | None = None,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """List scheduled posts with optional filtering."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        query = """
            SELECT sp.*, 
                   soc.content AS social_content, soc.platform AS social_platform,
                   a.topic AS article_topic, a.content AS article_content
            FROM scheduled_posts sp
            LEFT JOIN social_posts soc ON sp.social_post_id = soc.id
            LEFT JOIN articles a ON sp.article_id = a.id
            WHERE 1=1
        """
        params = []

        if status:
            query += " AND sp.status = ?"
            params.append(status)
        if platform:
            query += " AND sp.platform = ?"
            params.append(platform)

        query += " ORDER BY sp.scheduled_for ASC"
        cur = conn.execute(query, params)
        return cur.fetchall()


def get_pending_scheduled_posts(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Get all pending scheduled posts that are due (scheduled_for <= now)."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT sp.*, 
                   soc.content AS social_content, soc.platform AS social_platform,
                   a.topic AS article_topic, a.content AS article_content,
                   a.episode_id
            FROM scheduled_posts sp
            LEFT JOIN social_posts soc ON sp.social_post_id = soc.id
            LEFT JOIN articles a ON sp.article_id = a.id
            WHERE sp.status = 'pending' AND sp.scheduled_for <= ?
            ORDER BY sp.scheduled_for ASC
            """,
            (now,),
        )
        return cur.fetchall()


def update_scheduled_post_time(
    scheduled_id: int,
    scheduled_for: str,
    db_path: str = DB_PATH,
) -> bool:
    """Update the scheduled time for a pending post.
    
    Returns True if updated successfully, False if post not found or not pending.
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE scheduled_posts
            SET scheduled_for = ?
            WHERE id = ? AND status = 'pending'
            """,
            (scheduled_for, scheduled_id),
        )
        conn.commit()
        return cur.rowcount > 0


def update_scheduled_post_status(
    scheduled_id: int,
    status: str,
    linkedin_post_urn: str | None = None,
    error_message: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Update the status of a scheduled post."""
    with sqlite3.connect(db_path) as conn:
        posted_at = None
        if status == "posted":
            posted_at = datetime.utcnow().isoformat(timespec="seconds")

        conn.execute(
            """
            UPDATE scheduled_posts SET
                status = ?,
                linkedin_post_urn = ?,
                error_message = ?,
                posted_at = ?
            WHERE id = ?
            """,
            (status, linkedin_post_urn, error_message, posted_at, scheduled_id),
        )
        conn.commit()


def cancel_scheduled_post(scheduled_id: int, db_path: str = DB_PATH) -> bool:
    """Cancel a pending scheduled post. Returns True if cancelled."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE scheduled_posts SET status = 'cancelled'
            WHERE id = ? AND status = 'pending'
            """,
            (scheduled_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_scheduled_post(scheduled_id: int, db_path: str = DB_PATH) -> None:
    """Delete a scheduled post."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM scheduled_posts WHERE id = ?", (scheduled_id,))
        conn.commit()


def get_scheduled_posts_for_article(
    article_id: int,
    db_path: str = DB_PATH,
) -> List[sqlite3.Row]:
    """Get all scheduled posts for a specific article."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT sp.*, soc.content AS social_content
            FROM scheduled_posts sp
            LEFT JOIN social_posts soc ON sp.social_post_id = soc.id
            WHERE sp.article_id = ? OR soc.article_id = ?
            ORDER BY sp.scheduled_for ASC
            """,
            (article_id, article_id),
        )
        return cur.fetchall()


# --- Schedule Time Slots Functions ---


def add_time_slot(
    day_of_week: int,
    time_slot: str,
    enabled: bool = True,
    db_path: str = DB_PATH,
) -> int:
    """Add a new time slot for queue-based scheduling.
    
    Args:
        day_of_week: 0-6 (Monday-Sunday), or -1 for every day
        time_slot: Time in HH:MM format (24-hour)
        enabled: Whether the slot is active
    
    Returns:
        The ID of the created time slot
    """
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO schedule_time_slots (day_of_week, time_slot, enabled, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (day_of_week, time_slot, 1 if enabled else 0, created_at),
        )
        conn.commit()
        return cur.lastrowid


def list_time_slots(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Get all configured time slots ordered by day and time."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT * FROM schedule_time_slots
            ORDER BY day_of_week ASC, time_slot ASC
            """
        )
        return cur.fetchall()


def get_enabled_time_slots(db_path: str = DB_PATH) -> List[sqlite3.Row]:
    """Get only enabled time slots."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT * FROM schedule_time_slots
            WHERE enabled = 1
            ORDER BY day_of_week ASC, time_slot ASC
            """
        )
        return cur.fetchall()


def update_time_slot(
    slot_id: int,
    day_of_week: int | None = None,
    time_slot: str | None = None,
    enabled: bool | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Update a time slot's settings."""
    with sqlite3.connect(db_path) as conn:
        if day_of_week is not None:
            conn.execute(
                "UPDATE schedule_time_slots SET day_of_week = ? WHERE id = ?",
                (day_of_week, slot_id),
            )
        if time_slot is not None:
            conn.execute(
                "UPDATE schedule_time_slots SET time_slot = ? WHERE id = ?",
                (time_slot, slot_id),
            )
        if enabled is not None:
            conn.execute(
                "UPDATE schedule_time_slots SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, slot_id),
            )
        conn.commit()


def delete_time_slot(slot_id: int, db_path: str = DB_PATH) -> None:
    """Delete a time slot."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM schedule_time_slots WHERE id = ?", (slot_id,))
        conn.commit()


def get_next_available_slot(db_path: str = DB_PATH) -> str | None:
    """Find the next available time slot for scheduling.
    
    Returns the next datetime (ISO format) based on configured time slots
    that doesn't conflict with existing pending posts.
    
    Note: Uses LOCAL time for comparison since time slots are configured
    in local time by the user.
    
    Returns:
        ISO format datetime string, or None if no slots configured
    """
    from datetime import datetime, timedelta
    
    slots = get_enabled_time_slots(db_path)
    if not slots:
        return None
    
    # Get existing pending posts to check for conflicts
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT scheduled_for FROM scheduled_posts
            WHERE status = 'pending'
            """
        )
        existing_times = {row['scheduled_for'] for row in cur.fetchall()}
    
    # Use local time since time slots are configured in local time
    now = datetime.now()
    # Look up to 30 days ahead
    for day_offset in range(30):
        check_date = now + timedelta(days=day_offset)
        current_day_of_week = check_date.weekday()  # 0=Monday, 6=Sunday
        
        for slot in slots:
            slot_day = slot['day_of_week']
            # -1 means every day
            if slot_day != -1 and slot_day != current_day_of_week:
                continue
            
            # Parse the time slot
            try:
                hour, minute = map(int, slot['time_slot'].split(':'))
            except (ValueError, AttributeError):
                continue
            
            # Create the candidate datetime
            candidate = check_date.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            
            # Skip if in the past
            if candidate <= now:
                continue
            
            # Check if this slot is already taken
            candidate_str = candidate.isoformat(timespec="seconds")
            if candidate_str not in existing_times:
                return candidate_str
    
    return None


def initialize_default_time_slots(db_path: str = DB_PATH) -> None:
    """Create default time slots if none exist.
    
    Default slots: 9:00 AM, 12:00 PM, 5:00 PM every day
    """
    existing = list_time_slots(db_path)
    if existing:
        return  # Already have slots configured
    
    default_times = ["09:00", "12:00", "17:00"]
    for time in default_times:
        add_time_slot(
            day_of_week=-1,  # Every day
            time_slot=time,
            enabled=True,
            db_path=db_path,
        )
