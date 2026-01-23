"""Flask web interface for the PodInsights tool."""

from __future__ import annotations
import os
import tempfile
import threading
from queue import Queue
from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import re
import feedparser
import requests
from datetime import datetime
import time
from html import unescape
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from database import (
    init_db,
    get_episode,
    get_episode_by_id,
    save_episode,
    queue_episode,
    update_episode_status,
    delete_episode_by_id,
    delete_episodes_bulk,
    reset_episode_for_reprocess,
    add_feed,
    list_feeds,
    get_feed_by_id,
    delete_feed,
    delete_feeds_bulk,
    add_ticket,
    list_tickets,
    delete_ticket,
    delete_tickets_bulk,
    list_all_episodes,
    add_article,
    get_article,
    list_articles,
    update_article,
    delete_article,
    update_feed_metadata,
    add_social_post,
    get_social_post,
    list_social_posts,
    delete_social_post,
    delete_social_posts_bulk,
    delete_social_posts_for_article,
    mark_social_post_used,
    update_social_post,
    # LinkedIn token functions
    save_linkedin_token,
    get_linkedin_token,
    delete_linkedin_token,
    update_linkedin_token,
    # Threads token functions
    save_threads_token,
    get_threads_token,
    delete_threads_token,
    update_threads_token,
    # Scheduled posts functions
    add_scheduled_post,
    get_scheduled_post,
    list_scheduled_posts,
    get_pending_scheduled_posts,
    update_scheduled_post_status,
    update_scheduled_post_time,
    cancel_scheduled_post,
    delete_scheduled_post,
    delete_scheduled_posts_bulk,
    clear_pending_scheduled_posts,
    get_scheduled_posts_for_article,
    # Time slot functions for queue-based scheduling
    add_time_slot,
    list_time_slots,
    get_enabled_time_slots,
    update_time_slot,
    delete_time_slot,
    get_next_available_slot,
    initialize_default_time_slots,
)
from podinsights import (
    transcribe_audio,
    summarize_text,
    extract_action_items,
    write_results_json,
    configure_logging,
    generate_article,
    generate_social_copy,
    refine_article,
)
from linkedin_client import (
    LinkedInClient,
    get_linkedin_client,
    calculate_token_expiry,
    is_token_expired,
)
from threads_client import (
    ThreadsClient,
    get_threads_client,
    calculate_token_expiry as threads_calculate_token_expiry,
    is_token_expired as threads_is_token_expired,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())
configure_logging()
init_db()

# Background processing queue used to process episodes without blocking the web request
task_queue: Queue = Queue()


def worker() -> None:
    """Background thread processing queued episodes."""
    while True:
        # Wait for an episode to appear in the queue
        item = task_queue.get()
        if item is None:
            break
        url = item["url"]
        title = item.get("title", "Episode")
        feed_id = item.get("feed_id")
        published = item.get("published")
        try:
            update_episode_status(url, "processing")
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = os.path.join(tmpdir, "episode.mp3")
                with requests.get(url, stream=True) as r:
                    r.raise_for_status()
                    with open(audio_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                transcript = transcribe_audio(audio_path)
                summary = summarize_text(transcript)
                actions = extract_action_items(transcript)
                out_path = os.path.join(tmpdir, "results.json")
                write_results_json(transcript, summary, actions, out_path)
                save_episode(url, title, transcript, summary, actions, feed_id, published)
        except Exception:
            app.logger.exception("Failed to process episode %s", url)
            update_episode_status(url, "error")
        finally:
            task_queue.task_done()


worker_thread = threading.Thread(target=worker, daemon=True)
# Start the worker when the application imports
worker_thread.start()


def strip_html(text: str) -> str:
    """Return plain text with HTML tags removed."""
    if not text:
        return ""
    # replace common HTML tags with line breaks then strip everything else
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def make_short_description(text: str, limit: int = 200) -> str:
    """Return a short preview from the provided text."""
    if not text:
        return ""
    text = text.strip()
    # Use the first couple of sentences as a human friendly snippet
    sentences = re.split(r"(?<=[.!?])\s+", text)
    short = " ".join(sentences[:2])
    if len(short) > limit:
        short = short[:limit].rstrip() + "..."
    return short


def fetch_article_content(url: str, timeout: int = 15) -> str:
    """Fetch and extract the main content from an article URL.
    
    Uses BeautifulSoup to extract readable article text from web pages.
    Returns extracted text or empty string on failure.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        
        # Check content type - only process HTML
        content_type = resp.headers.get('content-type', '').lower()
        if 'text/html' not in content_type and 'application/xhtml' not in content_type:
            return ""
        
        soup = BeautifulSoup(resp.content, 'lxml')
        
        # Remove unwanted elements
        for element in soup.find_all(['script', 'style', 'nav', 'header', 'footer', 
                                       'aside', 'iframe', 'noscript', 'form',
                                       'button', 'input', 'select', 'textarea']):
            element.decompose()
        
        # Remove common ad/tracking elements by class or id patterns
        for element in soup.find_all(class_=re.compile(r'(ad|ads|advert|banner|sidebar|comment|share|social|related|recommend|newsletter|popup|modal|cookie)', re.I)):
            element.decompose()
        for element in soup.find_all(id=re.compile(r'(ad|ads|advert|banner|sidebar|comment|share|social|related|recommend|newsletter|popup|modal|cookie)', re.I)):
            element.decompose()
        
        # Try to find the main content area
        article_content = None
        
        # Priority 1: Look for article tag
        article = soup.find('article')
        if article:
            article_content = article
        
        # Priority 2: Look for main content divs
        if not article_content:
            for selector in ['[role="main"]', '.article-content', '.post-content', 
                            '.entry-content', '.content', '#content', '.story-body',
                            '.article-body', '.post-body', 'main']:
                found = soup.select_one(selector)
                if found:
                    article_content = found
                    break
        
        # Priority 3: Use body as fallback
        if not article_content:
            article_content = soup.find('body') or soup
        
        # Extract text from paragraphs for cleaner output
        paragraphs = article_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre', 'code'])
        
        if paragraphs:
            text_parts = []
            for p in paragraphs:
                text = p.get_text(separator=' ', strip=True)
                if text and len(text) > 20:  # Filter out short fragments
                    text_parts.append(text)
            content = '\n\n'.join(text_parts)
        else:
            # Fallback: get all text
            content = article_content.get_text(separator='\n', strip=True)
        
        # Clean up whitespace
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        content = content.strip()
        
        return content
        
    except requests.exceptions.Timeout:
        app.logger.warning("Timeout fetching article: %s", url)
        return ""
    except requests.exceptions.RequestException as e:
        app.logger.warning("Failed to fetch article %s: %s", url, str(e))
        return ""
    except Exception as e:
        app.logger.exception("Error extracting content from %s: %s", url, str(e))
        return ""

def create_jira_issue(summary: str, description: str) -> dict:
    """Create a JIRA issue using credentials from environment variables."""
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    project = os.environ.get("JIRA_PROJECT_KEY")
    if not all([base, email, token, project]):
        raise RuntimeError("JIRA configuration is missing")

    url = f"{base}/rest/api/3/issue"
    data = {
        "fields": {
            "project": {"key": project},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": description
                            }
                        ]
                    }
                ]
            },
            "issuetype": {"name": "Task"},
        }
    }
    # Use basic auth with an API token
    resp = requests.post(url, json=data, auth=(email, token))
    resp.raise_for_status()
    return resp.json()


def get_jira_issue_status(issue_key: str) -> str:
    """Return the status name for a JIRA issue."""
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not all([base, email, token, issue_key]):
        return ""
    try:
        # Fetch the issue data from JIRA
        url = f"{base}/rest/api/3/issue/{issue_key}"
        resp = requests.get(url, auth=(email, token))
        resp.raise_for_status()
        data = resp.json()
        return data.get("fields", {}).get("status", {}).get("name", "")
    except Exception:  # pragma: no cover - external call
        app.logger.exception("Failed to fetch status for %s", issue_key)
        return ""


def get_jira_issue_transitions(issue_key: str) -> list[dict]:
    """Return available transitions for a JIRA issue."""
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not all([base, email, token, issue_key]):
        return []
    try:
        # Fetch transitions that allow moving the issue between states
        url = f"{base}/rest/api/3/issue/{issue_key}/transitions"
        resp = requests.get(url, auth=(email, token))
        resp.raise_for_status()
        data = resp.json()
        return [
            {"id": t.get("id"), "name": t.get("name")}
            for t in data.get("transitions", [])
        ]
    except Exception:  # pragma: no cover - external call
        app.logger.exception("Failed to fetch transitions for %s", issue_key)
        return []


def transition_jira_issue(issue_key: str, transition_id: str) -> None:
    """Move a JIRA issue to a new status via transition id."""
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not all([base, email, token, issue_key, transition_id]):
        return
    try:
        # Perform the transition request
        url = f"{base}/rest/api/3/issue/{issue_key}/transitions"
        data = {"transition": {"id": transition_id}}
        resp = requests.post(url, json=data, auth=(email, token))
        resp.raise_for_status()
    except Exception:  # pragma: no cover - external call
        app.logger.exception(
            "Failed to transition %s using id %s", issue_key, transition_id
        )

# Templates are stored in the ``templates`` directory

def refresh_feed_metadata(feed_id: int, feed_url: str) -> dict:
    """Fetch feed and update cached metadata. Returns the metadata dict."""
    try:
        feed_data = feedparser.parse(feed_url)
        if not feed_data.entries:
            return {'type': 'unknown', 'last_post': None, 'item_count': 0}
        
        # Determine feed type from first entry with content
        is_audio = False
        for entry in feed_data.entries[:5]:  # Check first 5 entries
            if entry.get('enclosures'):
                is_audio = True
                break
        
        feed_type = 'audio' if is_audio else 'text'
        
        # Get last post date from most recent entry
        last_post = None
        last_post_str = None
        for entry in feed_data.entries[:1]:
            if getattr(entry, 'published_parsed', None):
                last_post = datetime.fromtimestamp(time.mktime(entry.published_parsed))
                last_post_str = last_post.isoformat()
            elif getattr(entry, 'updated_parsed', None):
                last_post = datetime.fromtimestamp(time.mktime(entry.updated_parsed))
                last_post_str = last_post.isoformat()
        
        item_count = len(feed_data.entries)
        
        # Save to database
        update_feed_metadata(feed_id, feed_type, last_post_str, item_count)
        
        return {
            'type': feed_type,
            'last_post': last_post,
            'item_count': item_count
        }
    except Exception:
        return {'type': 'unknown', 'last_post': None, 'item_count': 0}


@app.route('/', methods=['GET', 'POST'])
def index():
    """List stored podcast feeds and allow new ones to be added."""
    if request.method == 'POST':
        # User submitted a new feed URL
        feed_url = request.form['feed_url']
        feed = feedparser.parse(feed_url)
        title = feed.feed.get('title', feed_url)
        feed_id = add_feed(feed_url, title)
        # Refresh metadata for the new feed
        refresh_feed_metadata(feed_id, feed_url)
        return redirect(url_for('view_feed', feed_id=feed_id))
    
    # Get filter and sort parameters
    filter_type = request.args.get('type', '')
    sort_by = request.args.get('sort', 'title')
    sort_order = request.args.get('order', 'asc')
    search_query = request.args.get('q', '').lower()
    
    raw_feeds = list_feeds()
    feeds_with_meta = []
    
    for f in raw_feeds:
        # Use cached metadata from database
        last_post = None
        if f['last_post']:
            try:
                last_post = datetime.fromisoformat(f['last_post'])
            except (ValueError, TypeError):
                pass
        
        feed_dict = {
            'id': f['id'],
            'title': f['title'],
            'url': f['url'],
            'type': f['feed_type'] or 'unknown',
            'last_post': last_post,
            'item_count': f['item_count'] or 0,
            'last_checked': f['last_checked'],
        }
        
        # Apply type filter
        if filter_type and feed_dict['type'] != filter_type:
            continue
        
        # Apply search filter
        if search_query and search_query not in feed_dict['title'].lower():
            continue
        
        feeds_with_meta.append(feed_dict)
    
    # Sort feeds
    if sort_by == 'last_post':
        feeds_with_meta.sort(
            key=lambda x: x['last_post'] or datetime.min,
            reverse=(sort_order == 'desc')
        )
    elif sort_by == 'type':
        feeds_with_meta.sort(
            key=lambda x: x['type'] or '',
            reverse=(sort_order == 'desc')
        )
    elif sort_by == 'items':
        feeds_with_meta.sort(
            key=lambda x: x['item_count'] or 0,
            reverse=(sort_order == 'desc')
        )
    else:  # Default: title
        feeds_with_meta.sort(
            key=lambda x: x['title'].lower(),
            reverse=(sort_order == 'desc')
        )
    
    return render_template(
        'feeds.html',
        feeds=feeds_with_meta,
        filter_type=filter_type,
        sort_by=sort_by,
        sort_order=sort_order,
        search_query=search_query,
        now=datetime.now(),
    )


@app.route('/feed/<int:feed_id>/delete', methods=['POST'])
def remove_feed(feed_id: int):
    """Delete a feed and all its associated data."""
    delete_feed(feed_id)
    return redirect(url_for('index'))


@app.route('/feeds/bulk-delete', methods=['POST'])
def bulk_delete_feeds():
    """Delete multiple feeds at once."""
    feed_ids = request.form.getlist('feed_ids', type=int)
    if feed_ids:
        delete_feeds_bulk(feed_ids)
    return redirect(url_for('index'))


@app.route('/feed/<int:feed_id>/refresh')
def refresh_feed(feed_id: int):
    """Refresh metadata for a specific feed."""
    feed = get_feed_by_id(feed_id)
    if feed:
        refresh_feed_metadata(feed_id, feed['url'])
    return redirect(url_for('index'))


@app.route('/feeds/refresh-all')
def refresh_all_feeds():
    """Refresh metadata for all feeds (runs in background thread)."""
    def refresh_worker():
        for f in list_feeds():
            try:
                refresh_feed_metadata(f['id'], f['url'])
            except Exception:
                pass
    
    thread = threading.Thread(target=refresh_worker, daemon=True)
    thread.start()
    return redirect(url_for('index'))


@app.route('/feed/<int:feed_id>')
def view_feed(feed_id: int):
    """Display episodes/articles for a particular feed with pagination."""
    feed = get_feed_by_id(feed_id)
    if not feed:
        return redirect(url_for('index'))
    
    # Refresh metadata when viewing a feed (it's just one feed, so it's fast)
    refresh_feed_metadata(feed_id, feed['url'])
    
    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    per_page = min(per_page, 50)  # Cap at 50 items per page
    
    feed_data = feedparser.parse(feed['url'])
    all_episodes = []
    is_text_feed = True  # Assume text feed, switch to audio if we find enclosures
    for entry in feed_data.entries:
        # Determine if this is an audio podcast or text feed
        has_audio = bool(entry.get('enclosures'))
        if has_audio:
            is_text_feed = False
            url = entry.enclosures[0].href
            item_type = 'audio'
        else:
            # Text feed - use link as unique identifier
            url = entry.get('link', entry.get('id', ''))
            item_type = 'text'
            if not url:
                continue
        ep_db = get_episode(url)
        status = {
            'transcribed': ep_db is not None and bool(ep_db['transcript']),
            'summarized': ep_db is not None and bool(ep_db['summary']),
            'actions': ep_db is not None and bool(ep_db['action_items']),
            'state': ep_db['status'] if ep_db else 'new',
        }
        # Get full content for text feeds, description for podcasts
        content = ''
        if hasattr(entry, 'content') and entry.content:
            content = entry.content[0].get('value', '')
        if not content:
            content = entry.get('summary') or entry.get('description', '')
        # Prefer the summary element but fall back to description
        desc = entry.get('summary') or entry.get('description', '')
        clean_desc = strip_html(desc)
        # Try a few different locations for artwork
        img = None
        if hasattr(entry, 'image') and getattr(entry.image, 'href', None):
            img = entry.image.href
        elif entry.get('itunes_image'):
            img = entry.itunes_image.get('href') if isinstance(entry.itunes_image, dict) else entry.itunes_image
        elif entry.get('media_thumbnail'):
            img = entry.media_thumbnail[0].get('url')
        elif entry.get('media_content'):
            img = entry.media_content[0].get('url')
        published_ts = None
        if getattr(entry, 'published_parsed', None):
            published_ts = datetime.fromtimestamp(time.mktime(entry.published_parsed))
        elif getattr(entry, 'updated_parsed', None):
            published_ts = datetime.fromtimestamp(time.mktime(entry.updated_parsed))
        published_iso = published_ts.isoformat() if published_ts else None
        # Get author if available
        author = entry.get('author', '')
        all_episodes.append({
            'title': entry.title,
            'description': desc,
            'content': content,
            'clean_description': clean_desc,
            'short_description': make_short_description(clean_desc),
            'image': img,
            'enclosure': url,
            'link': entry.get('link', ''),
            'author': author,
            'type': item_type,
            'status': status,
            'published': published_iso,
        })
    
    # Calculate pagination
    total_items = len(all_episodes)
    total_pages = (total_items + per_page - 1) // per_page  # Ceiling division
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    episodes = all_episodes[start_idx:end_idx]
    
    pagination = {
        'page': page,
        'per_page': per_page,
        'total_items': total_items,
        'total_pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if page < total_pages else None,
    }
    
    return render_template('feed.html', feed=feed, episodes=episodes, is_text_feed=is_text_feed, pagination=pagination)


@app.route('/enqueue')
def enqueue_episode():
    """Queue an episode for background processing."""
    audio_url = request.args.get('url')
    title = request.args.get('title', 'Episode')
    feed_id = request.args.get('feed_id', type=int)
    published = request.args.get('published')
    if not audio_url or feed_id is None:
        return redirect(url_for('index'))
    queue_episode(audio_url, title, feed_id, published)
    task_queue.put({'url': audio_url, 'title': title, 'feed_id': feed_id, 'published': published})
    return redirect(url_for('status_page'))


@app.route('/process_text')
def process_text_article():
    """Process a text article (no transcription needed)."""
    article_url = request.args.get('url')
    title = request.args.get('title', 'Article')
    feed_id = request.args.get('feed_id', type=int)
    published = request.args.get('published')
    description = ""
    content = ""

    if not article_url:
        return redirect(url_for('index'))

    app.logger.info("Processing text article: %s", article_url)

    # Fetch content from the feed first
    if feed_id:
        feed = get_feed_by_id(feed_id)
        if feed:
            feed_data = feedparser.parse(feed['url'])
            for entry in feed_data.entries:
                entry_url = entry.get('link', entry.get('id', ''))
                if entry_url == article_url:
                    desc = entry.get('summary') or entry.get('description', '')
                    description = strip_html(desc)
                    # Get full content from feed
                    if hasattr(entry, 'content') and entry.content:
                        content = entry.content[0].get('value', '')
                    if not content:
                        content = desc
                    content = strip_html(content)
                    break
    
    # If RSS content is too short (likely just metadata/link), fetch the actual article
    MIN_CONTENT_LENGTH = 500  # Minimum chars to consider content "full"
    if len(content) < MIN_CONTENT_LENGTH:
        app.logger.info("RSS content too short (%d chars), fetching from URL: %s", len(content), article_url)
        fetched_content = fetch_article_content(article_url)
        if fetched_content and len(fetched_content) > len(content):
            app.logger.info("Fetched %d chars from article URL", len(fetched_content))
            content = fetched_content
        else:
            app.logger.warning("Could not fetch better content from URL")

    # Reuse previously processed data if available
    existing = get_episode(article_url)
    if existing:
        transcript = existing["transcript"]
        summary = existing["summary"]
        actions = existing["action_items"].splitlines()
        tickets = [dict(t) for t in list_tickets(existing["id"])]
        for t in tickets:
            t["status"] = get_jira_issue_status(t["ticket_key"])
            t["transitions"] = get_jira_issue_transitions(t["ticket_key"])
        articles = [dict(a) for a in list_articles(existing["id"])]
        return render_template(
            'result.html',
            title=existing["title"],
            transcript=transcript,
            summary=summary,
            actions=actions,
            description=description,
            feed_id=feed_id,
            url=article_url,
            tickets=tickets,
            articles=articles,
            current_url=request.full_path,
            is_text=True,
            original_link=article_url,
        )

    if not content:
        app.logger.error("No content found for article: %s", article_url)
        return redirect(url_for('view_feed', feed_id=feed_id))

    # For text articles, content IS the transcript (no transcription needed)
    transcript = content
    app.logger.info("Article content loaded (%d chars)", len(transcript))

    app.logger.info("Generating summary")
    summary = summarize_text(transcript)
    app.logger.info("Summary complete")

    app.logger.info("Extracting action items")
    actions = extract_action_items(transcript)
    app.logger.info("Action item extraction complete")

    # Persist results
    save_episode(article_url, title, transcript, summary, actions, feed_id, published)

    return render_template(
        'result.html',
        title=title,
        transcript=transcript,
        summary=summary,
        actions=actions,
        description=description,
        feed_id=feed_id,
        url=article_url,
        tickets=[],
        articles=[],
        current_url=request.full_path,
        is_text=True,
        original_link=article_url,
    )


@app.route('/process')
def process_episode():
    """Process an episode synchronously and show the results."""
    audio_url = request.args.get('url')
    title = request.args.get('title', 'Episode')
    feed_id = request.args.get('feed_id', type=int)
    published = request.args.get('published')
    description = ""
    if not audio_url:
        return redirect(url_for('index'))
    app.logger.info("Processing episode: %s", audio_url)
    if not description and feed_id:
        feed = get_feed_by_id(feed_id)
        if feed:
            feed_data = feedparser.parse(feed['url'])
            for entry in feed_data.entries:
                if entry.get('enclosures') and entry.enclosures[0].href == audio_url:
                    desc = entry.get('summary') or entry.get('description', '')
                    description = strip_html(desc)
                    break
    # Reuse previously processed data if available
    existing = get_episode(audio_url)
    if existing:
        # Already processed - read results from the DB
        transcript = existing["transcript"]
        summary = existing["summary"]
        actions = existing["action_items"].splitlines()
        tickets = [dict(t) for t in list_tickets(existing["id"])]
        for t in tickets:
            t["status"] = get_jira_issue_status(t["ticket_key"])
            t["transitions"] = get_jira_issue_transitions(t["ticket_key"])
        articles = [dict(a) for a in list_articles(existing["id"])]
        return render_template(
            'result.html',
            title=existing["title"],
            transcript=transcript,
            summary=summary,
            actions=actions,
            description=description,
            feed_id=feed_id,
            url=audio_url,
            tickets=tickets,
            articles=articles,
            current_url=request.full_path,
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, 'episode.mp3')
        with requests.get(audio_url, stream=True) as r:
            r.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        # Run the same processing pipeline as the CLI
        app.logger.info("Transcribing audio")
        transcript = transcribe_audio(audio_path)
        app.logger.info("Transcription complete")

        app.logger.info("Generating summary")
        summary = summarize_text(transcript)
        app.logger.info("Summary complete")

        app.logger.info("Extracting action items")
        actions = extract_action_items(transcript)
        app.logger.info("Action item extraction complete")
        out_path = os.path.join(tmpdir, 'results.json')
        write_results_json(transcript, summary, actions, out_path)
        # Persist results so they can be reused later
        save_episode(audio_url, title, transcript, summary, actions, feed_id, published)
    return render_template(
        'result.html',
        title=title,
        transcript=transcript,
        summary=summary,
        actions=actions,
        description=description,
        feed_id=feed_id,
        url=audio_url,
        tickets=[],
        articles=[],
        current_url=request.full_path,
    )


@app.route('/create_jira', methods=['POST'])
def create_jira():
    """Create JIRA tickets for the selected action items."""
    # Items are the action item texts selected by the user
    items = request.form.getlist('items')
    episode_url = request.form.get('episode_url')
    title = request.form.get('title', 'Episode')
    if not items:
        return redirect(request.referrer or url_for('index'))
    episode = get_episode(episode_url) if episode_url else None
    episode_id = episode['id'] if episode else None
    summary_text = episode['summary'] if episode else ''
    
    # Get the source (feed/podcast name)
    source_name = ''
    if episode and episode['feed_id']:
        feed = get_feed_by_id(episode['feed_id'])
        if feed:
            source_name = feed['title']
    
    created = []
    for item in items:
        try:
            # Build description with source if available
            source_line = f"Source: {source_name}\n" if source_name else ""
            description = (
                f"Action item: {item}\n\n"
                f"{source_line}"
                f"From episode: {title}\n\n"
                f"Episode summary:\n{summary_text}"
            )
            issue = create_jira_issue(item, description)
            key = issue.get('key', '')
            ticket_url = f"{os.environ.get('JIRA_BASE_URL')}/browse/{key}" if key else ''
            if episode_id is not None and key:
                add_ticket(episode_id, item, key, ticket_url)
            created.append({'key': key, 'url': ticket_url})
        except Exception as exc:  # pragma: no cover - external call
            created.append({'error': str(exc)})
    return render_template('jira_result.html', created=created)


@app.route('/update_ticket', methods=['POST'])
def update_ticket():
    """Update a JIRA ticket's status using a selected transition."""
    # Ticket key and selected transition id from the form
    ticket_key = request.form.get('ticket_key')
    transition_id = request.form.get('transition_id')
    ref = request.form.get('ref') or url_for('view_tickets')
    if ticket_key and transition_id:
        transition_jira_issue(ticket_key, transition_id)
    return redirect(ref)


@app.route('/tickets/<int:ticket_id>/delete', methods=['POST'])
def delete_ticket_route(ticket_id: int):
    """Delete a single JIRA ticket from the database."""
    success = delete_ticket(ticket_id)
    if success:
        return jsonify({"success": True, "message": "Ticket deleted"})
    return jsonify({"error": "Ticket not found"}), 404


@app.route('/tickets/delete-selected', methods=['POST'])
def delete_tickets_selected():
    """Delete multiple selected JIRA tickets."""
    data = request.get_json()
    ticket_ids = data.get('ticket_ids', []) if data else []
    
    if not ticket_ids:
        return jsonify({"error": "No tickets selected"}), 400
    
    try:
        ticket_ids = [int(tid) for tid in ticket_ids]
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid ticket IDs"}), 400
    
    count = delete_tickets_bulk(ticket_ids)
    return jsonify({
        "success": True,
        "message": f"Deleted {count} ticket{'s' if count != 1 else ''}"
    })


@app.route('/status')
def status_page():
    """Display processing status for all episodes."""
    sort = request.args.get('sort', 'released')
    sort_order = request.args.get('order', 'desc')
    filter_status = request.args.get('status', '')
    filter_feed = request.args.get('feed', '', type=str)
    filter_type = request.args.get('type', '')
    search_query = request.args.get('q', '').lower()
    
    if sort == 'released':
        order_by = 'published'
    elif sort == 'processed':
        order_by = 'processed_at'
    else:
        order_by = 'id'
    
    all_episodes = list_all_episodes(order_by=order_by)
    feeds_list = list_feeds()
    feeds = {f["id"]: f["title"] for f in feeds_list}
    
    # Filter episodes
    filtered_episodes = []
    for ep in all_episodes:
        # Status filter
        if filter_status and ep['status'] != filter_status:
            continue
        
        # Feed filter
        if filter_feed and str(ep['feed_id']) != filter_feed:
            continue
        
        # Type filter (audio vs text)
        is_audio = ep['url'].lower().split('?')[0].endswith(('.mp3', '.m4a', '.wav', '.ogg', '.aac', '.flac'))
        ep_type = 'audio' if is_audio else 'text'
        if filter_type and ep_type != filter_type:
            continue
        
        # Search filter
        if search_query and search_query not in (ep['title'] or '').lower():
            continue
        
        filtered_episodes.append(ep)
    
    # Reverse order if ascending
    if sort_order == 'asc':
        filtered_episodes = list(reversed(filtered_episodes))
    
    return render_template(
        'status.html',
        episodes=filtered_episodes,
        feeds=feeds,
        feeds_list=feeds_list,
        sort=sort,
        sort_order=sort_order,
        filter_status=filter_status,
        filter_feed=filter_feed,
        filter_type=filter_type,
        search_query=search_query,
    )


@app.route('/episode/<int:episode_id>/reprocess')
def reprocess_episode(episode_id: int):
    """Reprocess an episode - clears existing data and requeues."""
    episode = get_episode_by_id(episode_id)
    if not episode:
        return redirect(url_for('status_page'))

    # Detect if this is audio or text
    audio_extensions = ('.mp3', '.m4a', '.wav', '.ogg', '.aac', '.flac')
    is_audio = episode['url'].lower().split('?')[0].endswith(audio_extensions)

    # Reset episode data for reprocessing
    reset_episode_for_reprocess(episode_id)

    if is_audio:
        # Queue for background processing
        task_queue.put({
            'url': episode['url'],
            'title': episode['title'],
            'feed_id': episode['feed_id'],
            'published': episode['published'],
        })
        return redirect(url_for('status_page'))
    else:
        # Process text article directly
        return redirect(url_for(
            'process_text_article',
            url=episode['url'],
            title=episode['title'],
            feed_id=episode['feed_id'],
            published=episode['published'],
        ))


@app.route('/episode/<int:episode_id>/delete')
def delete_episode(episode_id: int):
    """Delete an episode and all associated data."""
    delete_episode_by_id(episode_id)
    return redirect(url_for('status_page'))


@app.route('/episodes/bulk-delete', methods=['POST'])
def bulk_delete_episodes():
    """Delete multiple episodes at once."""
    episode_ids = request.form.getlist('episode_ids', type=int)
    if episode_ids:
        delete_episodes_bulk(episode_ids)
    return redirect(url_for('status_page'))


@app.route('/tickets')
def view_tickets():
    """Display all created JIRA tickets."""
    sort_by = request.args.get('sort', 'id')
    sort_order = request.args.get('order', 'desc')
    filter_status = request.args.get('status', '')
    search_query = request.args.get('q', '').lower()
    
    raw_tickets = [dict(t) for t in list_tickets()]
    
    # Fetch JIRA statuses and filter
    tickets = []
    all_statuses = set()
    
    for t in raw_tickets:
        t["status"] = get_jira_issue_status(t["ticket_key"])
        t["transitions"] = get_jira_issue_transitions(t["ticket_key"])
        all_statuses.add(t["status"] or "Unknown")
        
        # Filter by status
        if filter_status and t["status"] != filter_status:
            continue
        
        # Search filter
        if search_query:
            searchable = f"{t['episode_title']} {t['action_item']} {t['ticket_key']}".lower()
            if search_query not in searchable:
                continue
        
        tickets.append(t)
    
    # Sort tickets
    if sort_by == 'episode':
        tickets.sort(key=lambda x: (x['episode_title'] or '').lower(), reverse=(sort_order == 'desc'))
    elif sort_by == 'status':
        tickets.sort(key=lambda x: (x['status'] or '').lower(), reverse=(sort_order == 'desc'))
    elif sort_by == 'ticket':
        tickets.sort(key=lambda x: x['ticket_key'], reverse=(sort_order == 'desc'))
    else:
        tickets.sort(key=lambda x: x['id'], reverse=(sort_order == 'desc'))
    
    return render_template(
        'tickets.html',
        tickets=tickets,
        sort_by=sort_by,
        sort_order=sort_order,
        filter_status=filter_status,
        search_query=search_query,
        all_statuses=sorted(all_statuses),
    )


@app.route('/generate_article', methods=['POST'])
def create_article():
    """Generate an article based on podcast or text article content."""
    episode_url = request.form.get('episode_url')
    topic = request.form.get('topic', '').strip()
    style = request.form.get('style', 'blog')
    extra_context = request.form.get('extra_context', '').strip()

    if not episode_url or not topic:
        return redirect(request.referrer or url_for('index'))

    episode = get_episode(episode_url)
    if not episode:
        return redirect(url_for('index'))

    # Detect if this is a text article (not an audio file)
    audio_extensions = ('.mp3', '.m4a', '.wav', '.ogg', '.aac', '.flac')
    is_text_source = not episode_url.lower().split('?')[0].endswith(audio_extensions)

    # Get the podcast/publication title for attribution
    feed = get_feed_by_id(episode['feed_id']) if episode['feed_id'] else None
    podcast_title = feed['title'] if feed else ("the publication" if is_text_source else "the podcast")
    episode_title = episode['title'] or ("this article" if is_text_source else "this episode")

    try:
        # Generate article using OpenAI
        article_content = generate_article(
            transcript=episode['transcript'],
            summary=episode['summary'],
            topic=topic,
            podcast_title=podcast_title,
            episode_title=episode_title,
            style=style,
            extra_context=extra_context if extra_context else None,
            is_text_source=is_text_source,
        )
        # Save article to database
        article_id = add_article(
            episode_id=episode['id'],
            topic=topic,
            style=style,
            content=article_content,
        )
        return redirect(url_for('view_article', article_id=article_id))
    except Exception as exc:
        app.logger.exception("Failed to generate article")
        return render_template(
            'article_error.html',
            error=str(exc),
            episode_url=episode_url,
            feed_id=episode['feed_id'],
        )


@app.route('/article/<int:article_id>')
def view_article(article_id: int):
    """Display a generated article."""
    article = get_article(article_id)
    if not article:
        return redirect(url_for('index'))
    
    # Get saved social posts grouped by platform
    posts = list_social_posts(article_id)
    social_posts = {}
    for post in posts:
        platform = post['platform']
        if platform not in social_posts:
            social_posts[platform] = []
        social_posts[platform].append({
            'id': post['id'],
            'content': post['content'],
            'created_at': post['created_at'],
            'used': bool(post['used']),
        })
    
    return render_template('article.html', article=dict(article), social_posts=social_posts)


@app.route('/article/<int:article_id>/edit', methods=['GET', 'POST'])
def edit_article(article_id: int):
    """Edit an existing article."""
    article = get_article(article_id)
    if not article:
        return redirect(url_for('view_articles'))
    
    if request.method == 'POST':
        topic = request.form.get('topic', '').strip()
        style = request.form.get('style', '').strip()
        content = request.form.get('content', '').strip()
        
        if topic and content:
            update_article(article_id, topic=topic, style=style, content=content)
            return redirect(url_for('view_article', article_id=article_id))
    
    return render_template('article_edit.html', article=dict(article))


@app.route('/article/<int:article_id>/delete', methods=['POST'])
def remove_article(article_id: int):
    """Delete an article."""
    delete_article(article_id)
    return redirect(url_for('view_articles'))


@app.route('/article/<int:article_id>/social', methods=['POST'])
def generate_article_social(article_id: int):
    """Generate social media promotional copy for an article and save to database."""
    article = get_article(article_id)
    if not article:
        return {"error": "Article not found"}, 404
    
    platforms = request.form.getlist('platforms')
    if not platforms:
        platforms = ["twitter", "linkedin", "facebook", "threads", "bluesky"]
    
    # Get number of posts per platform (default to 1, max 21)
    posts_per_platform = request.form.get('posts_per_platform', 1, type=int)
    posts_per_platform = max(1, min(posts_per_platform, 21))
    
    # Get optional extra context for the prompt
    extra_context = request.form.get('extra_context', '').strip() or None
    
    try:
        social_copy = generate_social_copy(
            article_content=article['content'],
            article_topic=article['topic'],
            platforms=platforms,
            posts_per_platform=posts_per_platform,
            extra_context=extra_context,
        )
        
        # Save generated posts to database
        saved_posts = {}
        for platform, copy_data in social_copy.items():
            posts = copy_data if isinstance(copy_data, list) else [copy_data]
            saved_posts[platform] = []
            for post_content in posts:
                post_id = add_social_post(
                    article_id=article_id,
                    platform=platform,
                    content=post_content,
                )
                saved_posts[platform].append({
                    'id': post_id,
                    'content': post_content,
                })
        
        return {
            "success": True,
            "social_copy": social_copy,
            "saved_posts": saved_posts,
            "posts_per_platform": posts_per_platform,
        }
    except Exception as exc:
        app.logger.exception("Failed to generate social media copy")
        return {"error": str(exc)}, 500


@app.route('/article/<int:article_id>/social/list', methods=['GET'])
def list_article_social_posts(article_id: int):
    """Get all saved social posts for an article."""
    article = get_article(article_id)
    if not article:
        return {"error": "Article not found"}, 404
    
    posts = list_social_posts(article_id)
    
    # Group posts by platform
    grouped = {}
    for post in posts:
        platform = post['platform']
        if platform not in grouped:
            grouped[platform] = []
        grouped[platform].append({
            'id': post['id'],
            'content': post['content'],
            'created_at': post['created_at'],
            'used': bool(post['used']),
        })
    
    return {"success": True, "posts": grouped}


@app.route('/social/<int:post_id>/delete', methods=['POST'])
def delete_social_post_route(post_id: int):
    """Delete a single social post."""
    delete_social_post(post_id)
    return {"success": True}


@app.route('/social/bulk-delete', methods=['POST'])
def bulk_delete_social_posts():
    """Delete multiple social posts at once."""
    post_ids = request.form.getlist('post_ids', type=int)
    if post_ids:
        count = delete_social_posts_bulk(post_ids)
        return {"success": True, "deleted": count}
    return {"success": True, "deleted": 0}


@app.route('/article/<int:article_id>/social/clear', methods=['POST'])
def clear_article_social_posts(article_id: int):
    """Delete all social posts for an article."""
    count = delete_social_posts_for_article(article_id)
    return {"success": True, "deleted": count}


@app.route('/social/<int:post_id>/toggle-used', methods=['POST'])
def toggle_social_post_used(post_id: int):
    """Toggle the used status of a social post."""
    post = get_social_post(post_id)
    if not post:
        return {"error": "Post not found"}, 404
    
    new_used = not bool(post['used'])
    mark_social_post_used(post_id, new_used)
    return {"success": True, "used": new_used}


@app.route('/social/<int:post_id>/edit', methods=['POST'])
def edit_social_post(post_id: int):
    """Update the content of a social post."""
    post = get_social_post(post_id)
    if not post:
        return {"error": "Post not found"}, 404
    
    content = request.form.get('content', '').strip()
    if not content:
        return {"error": "Content cannot be empty"}, 400
    
    update_social_post(post_id, content)
    return {"success": True, "content": content}


@app.route('/article/<int:article_id>/refine', methods=['POST'])
def refine_article_with_ai(article_id: int):
    """Refine an article using AI based on user feedback."""
    article = get_article(article_id)
    if not article:
        return {"error": "Article not found"}, 404
    
    feedback = request.form.get('feedback', '').strip()
    if not feedback:
        return {"error": "Please provide feedback for how to refine the article"}, 400
    
    try:
        refined_content = refine_article(
            current_content=article['content'],
            user_feedback=feedback,
            article_topic=article['topic'],
        )
        
        # Optionally auto-save the refined content
        auto_save = request.form.get('auto_save', 'false') == 'true'
        if auto_save:
            update_article(article_id, content=refined_content)
        
        return {"success": True, "refined_content": refined_content, "saved": auto_save}
    except Exception as exc:
        app.logger.exception("Failed to refine article")
        return {"error": str(exc)}, 500


@app.route('/articles')
def view_articles():
    """Display all generated articles."""
    sort_by = request.args.get('sort', 'date')
    sort_order = request.args.get('order', 'desc')
    filter_style = request.args.get('style', '')
    filter_podcast = request.args.get('podcast', '')
    search_query = request.args.get('q', '').lower()
    
    raw_articles = [dict(a) for a in list_articles()]
    
    # Collect unique styles and podcasts for filter dropdowns
    all_styles = set()
    all_podcasts = set()
    
    articles = []
    for a in raw_articles:
        all_styles.add(a['style'])
        if a.get('podcast_title'):
            all_podcasts.add(a['podcast_title'])
        
        # Filter by style
        if filter_style and a['style'] != filter_style:
            continue
        
        # Filter by podcast
        if filter_podcast and a.get('podcast_title') != filter_podcast:
            continue
        
        # Search filter
        if search_query:
            searchable = f"{a['topic']} {a.get('episode_title', '')} {a.get('podcast_title', '')}".lower()
            if search_query not in searchable:
                continue
        
        articles.append(a)
    
    # Sort articles
    if sort_by == 'topic':
        articles.sort(key=lambda x: x['topic'].lower(), reverse=(sort_order == 'desc'))
    elif sort_by == 'style':
        articles.sort(key=lambda x: x['style'].lower(), reverse=(sort_order == 'desc'))
    elif sort_by == 'podcast':
        articles.sort(key=lambda x: (x.get('podcast_title') or '').lower(), reverse=(sort_order == 'desc'))
    else:  # Default: date
        articles.sort(key=lambda x: x['created_at'] or '', reverse=(sort_order == 'desc'))
    
    return render_template(
        'articles.html',
        articles=articles,
        sort_by=sort_by,
        sort_order=sort_order,
        filter_style=filter_style,
        filter_podcast=filter_podcast,
        search_query=search_query,
        all_styles=sorted(all_styles),
        all_podcasts=sorted(all_podcasts),
    )


# ============================================================================
# LinkedIn Integration Routes
# ============================================================================


@app.route('/linkedin/status')
def linkedin_status():
    """Check LinkedIn connection status."""
    client = get_linkedin_client()
    token = get_linkedin_token()
    
    if not client.is_configured():
        return jsonify({
            "connected": False,
            "configured": False,
            "message": "LinkedIn credentials not configured. Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET.",
        })
    
    if not token:
        return jsonify({
            "connected": False,
            "configured": True,
            "message": "Not connected to LinkedIn",
        })
    
    # Check if token is expired
    if is_token_expired(token['expires_at']):
        # Try to refresh if we have a refresh token
        if token['refresh_token']:
            try:
                new_token = client.refresh_access_token(token['refresh_token'])
                expires_at = calculate_token_expiry(new_token.get('expires_in', 5184000))
                update_linkedin_token(
                    access_token=new_token['access_token'],
                    expires_at=expires_at,
                    refresh_token=new_token.get('refresh_token'),
                )
                return jsonify({
                    "connected": True,
                    "configured": True,
                    "display_name": token['display_name'],
                    "email": token['email'],
                    "message": "Connected (token refreshed)",
                })
            except Exception as e:
                app.logger.warning("Failed to refresh LinkedIn token: %s", e)
                return jsonify({
                    "connected": False,
                    "configured": True,
                    "message": "Token expired. Please reconnect.",
                })
        else:
            return jsonify({
                "connected": False,
                "configured": True,
                "message": "Token expired. Please reconnect.",
            })
    
    # Check if user_urn is configured (needed for posting)
    needs_configuration = not token['user_urn']
    
    return jsonify({
        "connected": True,
        "configured": True,
        "needs_configuration": needs_configuration,
        "display_name": token['display_name'],
        "email": token['email'],
        "user_urn": token['user_urn'],
        "configure_url": url_for('linkedin_configure') if needs_configuration else None,
    })


@app.route('/linkedin/auth')
def linkedin_auth():
    """Start LinkedIn OAuth flow."""
    client = get_linkedin_client()
    
    if not client.is_configured():
        return jsonify({"error": "LinkedIn not configured"}), 400
    
    auth_url, state = client.get_authorization_url()
    session['linkedin_oauth_state'] = state
    
    return redirect(auth_url)


@app.route('/linkedin/callback')
def linkedin_callback():
    """Handle LinkedIn OAuth callback."""
    error = request.args.get('error')
    if error:
        error_desc = request.args.get('error_description', 'Unknown error')
        app.logger.error("LinkedIn OAuth error: %s - %s", error, error_desc)
        return render_template(
            'article_error.html',
            error=f"LinkedIn authorization failed: {error_desc}",
        )
    
    code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state to prevent CSRF
    stored_state = session.pop('linkedin_oauth_state', None)
    if not stored_state or stored_state != state:
        app.logger.warning("LinkedIn OAuth state mismatch")
        return render_template(
            'article_error.html',
            error="Security verification failed. Please try again.",
        )
    
    if not code:
        return render_template(
            'article_error.html',
            error="No authorization code received from LinkedIn.",
        )
    
    client = get_linkedin_client()
    
    try:
        # Exchange code for token
        token_data = client.exchange_code_for_token(code)
        access_token = token_data['access_token']
        expires_in = token_data.get('expires_in', 5184000)  # Default 60 days
        refresh_token = token_data.get('refresh_token')
        
        # Calculate expiry
        expires_at = calculate_token_expiry(expires_in)
        
        # Try to get user info - may return None if only w_member_social scope
        user_info = client.get_user_info(access_token)
        
        if user_info:
            member_id = user_info.get('sub') or user_info.get('id', '')
            user_urn = f"urn:li:person:{member_id}" if member_id else ''
            display_name = user_info.get('name', '') or user_info.get('localizedFirstName', 'LinkedIn User')
            email = user_info.get('email', '')
        else:
            # Profile endpoints didn't work - user needs to manually configure
            member_id = ''
            user_urn = ''
            display_name = 'LinkedIn User (needs configuration)'
            email = ''
        
        # Save token (with or without profile info)
        save_linkedin_token(
            access_token=access_token,
            expires_at=expires_at,
            member_id=member_id,
            user_urn=user_urn,
            display_name=display_name,
            email=email,
            refresh_token=refresh_token,
        )
        
        app.logger.info("LinkedIn connected for user: %s", display_name)
        
        # If we couldn't get profile info, redirect to configuration page
        if not user_urn:
            return redirect(url_for('linkedin_configure') + '?new=1')
        
        # Redirect to articles page with success message
        return redirect(url_for('view_articles') + '?linkedin=connected')
        
    except Exception as e:
        app.logger.exception("LinkedIn OAuth exchange failed")
        return render_template(
            'article_error.html',
            error=f"Failed to connect to LinkedIn: {str(e)}",
        )


@app.route('/linkedin/disconnect', methods=['POST'])
def linkedin_disconnect():
    """Disconnect LinkedIn account."""
    delete_linkedin_token()
    return jsonify({"success": True, "message": "LinkedIn disconnected"})


@app.route('/linkedin/configure', methods=['GET', 'POST'])
def linkedin_configure():
    """Configure LinkedIn member ID manually.
    
    This is needed when the user only has 'Share on LinkedIn' product
    which doesn't provide profile access scopes.
    """
    from database import update_linkedin_member_urn
    
    token = get_linkedin_token()
    if not token:
        return redirect(url_for('view_schedule') + '?error=not_connected')
    
    if request.method == 'POST':
        member_id = request.form.get('member_id', '').strip()
        display_name = request.form.get('display_name', '').strip() or 'LinkedIn User'
        
        if not member_id:
            return render_template(
                'linkedin_configure.html',
                token=token,
                error="Member ID is required",
                is_new=request.args.get('new') == '1',
            )
        
        # Update the token with the manual member ID
        success = update_linkedin_member_urn(
            member_id=member_id,
            display_name=display_name,
        )
        
        if success:
            app.logger.info("LinkedIn member ID configured manually: %s", member_id)
            return redirect(url_for('view_schedule') + '?linkedin=configured')
        else:
            return render_template(
                'linkedin_configure.html',
                token=token,
                error="Failed to save configuration",
                is_new=request.args.get('new') == '1',
            )
    
    # GET request - show configuration form
    return render_template(
        'linkedin_configure.html',
        token=token,
        is_new=request.args.get('new') == '1',
    )


@app.route('/linkedin/post/<int:post_id>', methods=['POST'])
def linkedin_post_social(post_id: int):
    """Post a social media post to LinkedIn immediately."""
    post = get_social_post(post_id)
    if not post:
        return jsonify({"error": "Post not found"}), 404
    
    token = get_linkedin_token()
    if not token:
        return jsonify({"error": "LinkedIn not connected"}), 401
    
    if is_token_expired(token['expires_at']):
        return jsonify({"error": "LinkedIn token expired. Please reconnect."}), 401
    
    if not token['user_urn']:
        return jsonify({
            "error": "LinkedIn needs configuration. Please configure your Member ID.",
            "configure_url": url_for('linkedin_configure')
        }), 400
    
    client = get_linkedin_client()
    
    try:
        # Use smart post to automatically detect URLs and show link previews
        # Pass the article topic as fallback title for link previews
        article_topic = post['article_topic'] if 'article_topic' in post.keys() else None
        result = client.create_smart_post(
            access_token=token['access_token'],
            author_urn=token['user_urn'],
            text=post['content'],
            article_title=article_topic,
        )
        
        if result['success']:
            # Mark the post as used
            mark_social_post_used(post_id, True)
            return jsonify({
                "success": True,
                "post_urn": result['post_urn'],
                "message": "Posted to LinkedIn successfully!",
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get('error', 'Unknown error'),
            }), 400
            
    except Exception as e:
        app.logger.exception("Failed to post to LinkedIn")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Threads Integration Routes
# ============================================================================


@app.route('/threads/status')
def threads_status():
    """Check Threads connection status."""
    client = get_threads_client()
    token = get_threads_token()
    
    if not client.is_configured():
        return jsonify({
            "connected": False,
            "configured": False,
            "message": "Threads credentials not configured. Set THREADS_APP_ID and THREADS_APP_SECRET.",
        })
    
    if not token:
        return jsonify({
            "connected": False,
            "configured": True,
            "message": "Not connected to Threads",
        })
    
    # Check if token is expired
    if threads_is_token_expired(token['expires_at']):
        # Try to refresh the token
        try:
            new_token = client.refresh_access_token(token['access_token'])
            expires_at = threads_calculate_token_expiry(new_token.get('expires_in', 5184000))
            update_threads_token(
                access_token=new_token['access_token'],
                expires_at=expires_at,
            )
            return jsonify({
                "connected": True,
                "configured": True,
                "username": token['username'],
                "display_name": token['display_name'],
                "profile_picture_url": token['profile_picture_url'],
                "message": "Connected (token refreshed)",
            })
        except Exception as e:
            app.logger.warning("Failed to refresh Threads token: %s", e)
            return jsonify({
                "connected": False,
                "configured": True,
                "message": "Token expired. Please reconnect.",
            })
    
    return jsonify({
        "connected": True,
        "configured": True,
        "username": token['username'],
        "display_name": token['display_name'],
        "profile_picture_url": token['profile_picture_url'],
        "user_id": token['user_id'],
    })


@app.route('/threads/auth')
def threads_auth():
    """Start Threads OAuth flow."""
    client = get_threads_client()
    
    if not client.is_configured():
        return jsonify({"error": "Threads not configured"}), 400
    
    auth_url, state = client.get_authorization_url()
    session['threads_oauth_state'] = state
    
    return redirect(auth_url)


@app.route('/threads/callback')
def threads_callback():
    """Handle Threads OAuth callback."""
    error = request.args.get('error')
    if error:
        error_desc = request.args.get('error_description', 'Unknown error')
        app.logger.error("Threads OAuth error: %s - %s", error, error_desc)
        return render_template(
            'article_error.html',
            error=f"Threads authorization failed: {error_desc}",
        )
    
    code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state to prevent CSRF
    stored_state = session.pop('threads_oauth_state', None)
    if not stored_state or stored_state != state:
        app.logger.warning("Threads OAuth state mismatch")
        return render_template(
            'article_error.html',
            error="Security verification failed. Please try again.",
        )
    
    if not code:
        return render_template(
            'article_error.html',
            error="No authorization code received from Threads.",
        )
    
    client = get_threads_client()
    
    try:
        # Step 1: Exchange code for short-lived token
        token_data = client.exchange_code_for_token(code)
        short_lived_token = token_data['access_token']
        user_id = token_data.get('user_id', '')
        
        # Step 2: Exchange for long-lived token (60 days)
        long_lived_data = client.get_long_lived_token(short_lived_token)
        access_token = long_lived_data['access_token']
        expires_in = long_lived_data.get('expires_in', 5184000)  # Default 60 days
        
        # Calculate expiry
        expires_at = threads_calculate_token_expiry(expires_in)
        
        # Get user profile
        user_info = client.get_user_profile(access_token)
        
        if user_info:
            user_id = user_info.get('id', user_id)
            username = user_info.get('username', '')
            display_name = user_info.get('name', '') or username
            profile_picture_url = user_info.get('threads_profile_picture_url', '')
        else:
            username = ''
            display_name = 'Threads User'
            profile_picture_url = ''
        
        # Save token
        save_threads_token(
            access_token=access_token,
            expires_at=expires_at,
            user_id=user_id,
            username=username,
            display_name=display_name,
            profile_picture_url=profile_picture_url,
        )
        
        app.logger.info("Threads connected for user: @%s", username)
        
        # Redirect to schedule page with success message
        return redirect(url_for('schedule_list') + '?threads=connected')
        
    except Exception as e:
        app.logger.exception("Threads OAuth exchange failed")
        return render_template(
            'article_error.html',
            error=f"Failed to connect to Threads: {str(e)}",
        )


@app.route('/threads/disconnect', methods=['POST'])
def threads_disconnect():
    """Disconnect Threads account."""
    delete_threads_token()
    return jsonify({"success": True, "message": "Threads disconnected"})


@app.route('/threads/post/<int:post_id>', methods=['POST'])
def threads_post_social(post_id: int):
    """Post a social media post to Threads immediately."""
    post = get_social_post(post_id)
    if not post:
        return jsonify({"error": "Post not found"}), 404
    
    # Check if this is a Threads post
    if post['platform'] != 'threads':
        return jsonify({"error": "This post is not for Threads"}), 400
    
    # Get Threads token
    token = get_threads_token()
    if not token:
        return jsonify({"error": "Threads not connected. Please connect your account first."}), 401
    
    # Check if token is expired and try to refresh
    if threads_is_token_expired(token['expires_at']):
        client = get_threads_client()
        try:
            new_token = client.refresh_access_token(token['access_token'])
            expires_at = threads_calculate_token_expiry(new_token.get('expires_in', 5184000))
            update_threads_token(
                access_token=new_token['access_token'],
                expires_at=expires_at,
            )
            token = get_threads_token()
        except Exception as e:
            app.logger.warning("Failed to refresh Threads token: %s", e)
            return jsonify({"error": "Threads token expired. Please reconnect."}), 401
    
    client = get_threads_client()
    
    try:
        result = client.publish_text_post(
            access_token=token['access_token'],
            text=post['content'],
        )
        
        if result['success']:
            # Mark the post as used
            mark_social_post_used(post_id, True)
            return jsonify({
                "success": True,
                "post_id": result.get('post_id'),
                "message": "Posted to Threads successfully!",
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get('error', 'Unknown error'),
            }), 400
            
    except Exception as e:
        app.logger.exception("Failed to post to Threads")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Scheduled Posts Routes
# ============================================================================


@app.route('/schedule/add', methods=['POST'])
def schedule_add():
    """Add a post to the schedule queue."""
    post_type = request.form.get('post_type')  # 'social' or 'article'
    scheduled_for = request.form.get('scheduled_for')  # ISO datetime (optional if using queue)
    social_post_id = request.form.get('social_post_id', type=int)
    article_id = request.form.get('article_id', type=int)
    use_queue = request.form.get('use_queue') == '1'  # Auto-schedule to next available slot
    platform = request.form.get('platform', 'linkedin')  # Platform: 'linkedin' or 'threads'
    
    # If using queue, get the next available time slot
    if use_queue or not scheduled_for:
        scheduled_for = get_next_available_slot()
        if not scheduled_for:
            return jsonify({
                "error": "No time slots configured. Please add posting times in the Schedule settings."
            }), 400
    
    if post_type == 'social' and not social_post_id:
        return jsonify({"error": "Social post ID is required"}), 400
    
    if post_type == 'article' and not article_id:
        return jsonify({"error": "Article ID is required"}), 400
    
    # Verify the post/article exists
    if social_post_id:
        post = get_social_post(social_post_id)
        if not post:
            return jsonify({"error": "Social post not found"}), 404
    
    if article_id:
        article = get_article(article_id)
        if not article:
            return jsonify({"error": "Article not found"}), 404
    
    try:
        scheduled_id = add_scheduled_post(
            scheduled_for=scheduled_for,
            post_type=post_type,
            social_post_id=social_post_id,
            article_id=article_id,
            platform=platform,
        )
        
        # Format the display time
        scheduled_for_display = scheduled_for
        try:
            dt = datetime.fromisoformat(scheduled_for)
            scheduled_for_display = dt.strftime('%A, %B %d at %I:%M %p')
        except (ValueError, TypeError):
            pass
        
        return jsonify({
            "success": True,
            "scheduled_id": scheduled_id,
            "scheduled_for": scheduled_for,
            "scheduled_for_display": scheduled_for_display,
            "message": f"Post scheduled for {scheduled_for_display}",
        })
        
    except Exception as e:
        app.logger.exception("Failed to schedule post")
        return jsonify({"error": str(e)}), 500


@app.route('/schedule')
def schedule_list():
    """View all scheduled posts."""
    status_filter = request.args.get('status', '')
    
    # Initialize default time slots if none exist
    initialize_default_time_slots()
    
    posts = list_scheduled_posts(status=status_filter if status_filter else None)
    
    # Convert to list of dicts and format dates
    scheduled = []
    for p in posts:
        post_dict = dict(p)
        # Parse scheduled_for for display
        if post_dict.get('scheduled_for'):
            try:
                dt = datetime.fromisoformat(post_dict['scheduled_for'])
                post_dict['scheduled_for_display'] = dt.strftime('%Y-%m-%d %H:%M')
            except (ValueError, TypeError):
                post_dict['scheduled_for_display'] = post_dict['scheduled_for']
        scheduled.append(post_dict)
    
    # Check LinkedIn connection status
    token = get_linkedin_token()
    linkedin_connected = token is not None and not is_token_expired(token['expires_at']) if token else False
    
    # Check Threads connection status
    threads_token = get_threads_token()
    threads_connected = threads_token is not None and not threads_is_token_expired(threads_token['expires_at']) if threads_token else False
    
    # Get configured time slots
    time_slots = list_time_slots()
    time_slots_list = []
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    for slot in time_slots:
        slot_dict = dict(slot)
        if slot_dict['day_of_week'] == -1:
            slot_dict['day_display'] = 'Every day'
        else:
            slot_dict['day_display'] = day_names[slot_dict['day_of_week']]
        time_slots_list.append(slot_dict)
    
    # Get next available slot for display
    next_slot = get_next_available_slot()
    next_slot_display = None
    if next_slot:
        try:
            dt = datetime.fromisoformat(next_slot)
            next_slot_display = dt.strftime('%A, %B %d at %I:%M %p')
        except (ValueError, TypeError):
            next_slot_display = next_slot
    
    return render_template(
        'schedule.html',
        scheduled_posts=scheduled,
        status_filter=status_filter,
        linkedin_connected=linkedin_connected,
        threads_connected=threads_connected,
        time_slots=time_slots_list,
        next_slot=next_slot,
        next_slot_display=next_slot_display,
    )


@app.route('/schedule/<int:scheduled_id>/cancel', methods=['POST'])
def schedule_cancel(scheduled_id: int):
    """Cancel a scheduled post."""
    success = cancel_scheduled_post(scheduled_id)
    
    if success:
        return jsonify({"success": True, "message": "Post cancelled"})
    else:
        return jsonify({"error": "Could not cancel post (may already be posted or cancelled)"}), 400


@app.route('/schedule/<int:scheduled_id>/delete', methods=['POST'])
def schedule_delete(scheduled_id: int):
    """Delete a scheduled post."""
    delete_scheduled_post(scheduled_id)
    return jsonify({"success": True, "message": "Post deleted"})


@app.route('/schedule/clear-queue', methods=['POST'])
def schedule_clear_queue():
    """Clear all pending scheduled posts."""
    count = clear_pending_scheduled_posts()
    return jsonify({
        "success": True, 
        "message": f"Cleared {count} pending post{'s' if count != 1 else ''} from queue"
    })


@app.route('/schedule/delete-selected', methods=['POST'])
def schedule_delete_selected():
    """Delete multiple selected scheduled posts."""
    data = request.get_json()
    post_ids = data.get('post_ids', []) if data else []
    
    if not post_ids:
        return jsonify({"error": "No posts selected"}), 400
    
    # Ensure all IDs are integers
    try:
        post_ids = [int(pid) for pid in post_ids]
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid post IDs"}), 400
    
    count = delete_scheduled_posts_bulk(post_ids)
    return jsonify({
        "success": True,
        "message": f"Deleted {count} post{'s' if count != 1 else ''}"
    })


@app.route('/schedule/<int:scheduled_id>/edit', methods=['POST'])
def schedule_edit(scheduled_id: int):
    """Edit the scheduled time for a pending post."""
    scheduled_for = request.form.get('scheduled_for', '').strip()
    
    if not scheduled_for:
        return jsonify({"error": "Scheduled time is required"}), 400
    
    # Validate datetime format
    try:
        dt = datetime.fromisoformat(scheduled_for.replace('Z', '+00:00'))
        # Ensure it's in the future
        if dt <= datetime.utcnow():
            return jsonify({"error": "Scheduled time must be in the future"}), 400
        # Normalize to ISO format
        scheduled_for = dt.isoformat(timespec="seconds")
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid datetime format"}), 400
    
    success = update_scheduled_post_time(scheduled_id, scheduled_for)
    
    if success:
        # Format the display time
        try:
            display = dt.strftime('%A, %B %d at %I:%M %p')
        except:
            display = scheduled_for
        
        return jsonify({
            "success": True,
            "scheduled_for": scheduled_for,
            "scheduled_for_display": display,
            "message": f"Post rescheduled for {display}",
        })
    else:
        return jsonify({
            "error": "Could not update post (may not be pending or not found)"
        }), 400


# ============================================================================
# Time Slot Management Routes
# ============================================================================


@app.route('/schedule/slots', methods=['GET'])
def schedule_slots():
    """Get all configured time slots."""
    slots = list_time_slots()
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    
    slots_list = []
    for slot in slots:
        slot_dict = dict(slot)
        if slot_dict['day_of_week'] == -1:
            slot_dict['day_display'] = 'Every day'
        else:
            slot_dict['day_display'] = day_names[slot_dict['day_of_week']]
        slots_list.append(slot_dict)
    
    return jsonify({"slots": slots_list})


@app.route('/schedule/slots/add', methods=['POST'])
def schedule_slot_add():
    """Add a new time slot."""
    day_of_week = request.form.get('day_of_week', type=int, default=-1)
    time_slot = request.form.get('time_slot', '').strip()
    
    if not time_slot:
        return jsonify({"error": "Time is required"}), 400
    
    # Validate time format (HH:MM)
    try:
        hour, minute = map(int, time_slot.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError()
        time_slot = f"{hour:02d}:{minute:02d}"
    except (ValueError, AttributeError):
        return jsonify({"error": "Invalid time format. Use HH:MM (24-hour)"}), 400
    
    # Validate day_of_week
    if day_of_week < -1 or day_of_week > 6:
        return jsonify({"error": "Invalid day of week"}), 400
    
    slot_id = add_time_slot(
        day_of_week=day_of_week,
        time_slot=time_slot,
        enabled=True,
    )
    
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    day_display = 'Every day' if day_of_week == -1 else day_names[day_of_week]
    
    return jsonify({
        "success": True,
        "slot": {
            "id": slot_id,
            "day_of_week": day_of_week,
            "day_display": day_display,
            "time_slot": time_slot,
            "enabled": True,
        }
    })


@app.route('/schedule/slots/<int:slot_id>/toggle', methods=['POST'])
def schedule_slot_toggle(slot_id: int):
    """Toggle a time slot's enabled state."""
    slots = list_time_slots()
    slot = next((s for s in slots if s['id'] == slot_id), None)
    
    if not slot:
        return jsonify({"error": "Slot not found"}), 404
    
    new_enabled = not bool(slot['enabled'])
    update_time_slot(slot_id, enabled=new_enabled)
    
    return jsonify({
        "success": True,
        "enabled": new_enabled,
    })


@app.route('/schedule/slots/<int:slot_id>/delete', methods=['POST'])
def schedule_slot_delete(slot_id: int):
    """Delete a time slot."""
    delete_time_slot(slot_id)
    return jsonify({"success": True, "message": "Slot deleted"})


@app.route('/schedule/next-slot', methods=['GET'])
def schedule_next_slot():
    """Get the next available posting slot."""
    next_slot = get_next_available_slot()
    
    if not next_slot:
        return jsonify({
            "available": False,
            "message": "No time slots configured",
        })
    
    try:
        dt = datetime.fromisoformat(next_slot)
        display = dt.strftime('%A, %B %d at %I:%M %p')
    except (ValueError, TypeError):
        display = next_slot
    
    return jsonify({
        "available": True,
        "scheduled_for": next_slot,
        "display": display,
    })


# ============================================================================
# Background Scheduler Worker
# ============================================================================


def scheduled_post_worker() -> None:
    """Background thread that processes scheduled posts."""
    import time as time_module
    
    while True:
        try:
            # Check for pending posts every 60 seconds
            time_module.sleep(60)
            
            # Get pending posts that are due
            pending = get_pending_scheduled_posts()
            
            if not pending:
                continue
            
            # Cache tokens to avoid repeated DB queries
            linkedin_token = None
            threads_token = None
            
            for post in pending:
                try:
                    platform = post.get('platform', 'linkedin')
                    
                    # Get article topic safely from sqlite3.Row
                    article_topic = post['article_topic'] if 'article_topic' in post.keys() else None
                    
                    # Determine content
                    if post['post_type'] == 'social' and post['social_content']:
                        content = post['social_content']
                    elif post['post_type'] == 'article' and post['article_content']:
                        content = f"{post['article_topic']}\n\n{post['article_content'][:2800]}"
                    else:
                        app.logger.warning("Scheduled post %d has no content", post['id'])
                        update_scheduled_post_status(
                            post['id'],
                            status='failed',
                            error_message='No content found',
                        )
                        continue
                    
                    result = None
                    
                    if platform == 'threads':
                        # Handle Threads posting
                        if threads_token is None:
                            threads_token = get_threads_token()
                        
                        if not threads_token:
                            app.logger.warning("Scheduled Threads post %d due but Threads not connected", post['id'])
                            update_scheduled_post_status(
                                post['id'],
                                status='failed',
                                error_message='Threads not connected',
                            )
                            continue
                        
                        # Check token expiry and refresh if needed
                        if threads_is_token_expired(threads_token['expires_at']):
                            threads_client = get_threads_client()
                            try:
                                new_token = threads_client.refresh_access_token(threads_token['access_token'])
                                expires_at = threads_calculate_token_expiry(new_token.get('expires_in', 5184000))
                                update_threads_token(
                                    access_token=new_token['access_token'],
                                    expires_at=expires_at,
                                )
                                threads_token = get_threads_token()
                            except Exception as e:
                                app.logger.error("Failed to refresh Threads token: %s", e)
                                update_scheduled_post_status(
                                    post['id'],
                                    status='failed',
                                    error_message='Threads token expired',
                                )
                                continue
                        
                        threads_client = get_threads_client()
                        result = threads_client.publish_text_post(
                            access_token=threads_token['access_token'],
                            text=content[:500],  # Threads has 500 char limit
                        )
                        
                        if result['success']:
                            update_scheduled_post_status(
                                post['id'],
                                status='posted',
                                linkedin_post_urn=result.get('post_id'),  # Reuse field for Threads post ID
                            )
                            if post['social_post_id']:
                                mark_social_post_used(post['social_post_id'], True)
                            app.logger.info("Scheduled Threads post %d published successfully", post['id'])
                        else:
                            error_msg = str(result.get('error', 'Unknown error'))[:500]
                            update_scheduled_post_status(
                                post['id'],
                                status='failed',
                                error_message=error_msg,
                            )
                            app.logger.error("Scheduled Threads post %d failed: %s", post['id'], error_msg)
                    
                    else:
                        # Handle LinkedIn posting (default)
                        if linkedin_token is None:
                            linkedin_token = get_linkedin_token()
                        
                        if not linkedin_token:
                            app.logger.warning("Scheduled LinkedIn post %d due but LinkedIn not connected", post['id'])
                            update_scheduled_post_status(
                                post['id'],
                                status='failed',
                                error_message='LinkedIn not connected',
                            )
                            continue
                        
                        # Check token expiry and refresh if needed
                        if is_token_expired(linkedin_token['expires_at']):
                            linkedin_client = get_linkedin_client()
                            if linkedin_token['refresh_token']:
                                try:
                                    new_token = linkedin_client.refresh_access_token(linkedin_token['refresh_token'])
                                    expires_at = calculate_token_expiry(new_token.get('expires_in', 5184000))
                                    update_linkedin_token(
                                        access_token=new_token['access_token'],
                                        expires_at=expires_at,
                                        refresh_token=new_token.get('refresh_token'),
                                    )
                                    linkedin_token = get_linkedin_token()
                                except Exception as e:
                                    app.logger.error("Failed to refresh LinkedIn token: %s", e)
                                    update_scheduled_post_status(
                                        post['id'],
                                        status='failed',
                                        error_message='LinkedIn token expired',
                                    )
                                    continue
                            else:
                                app.logger.warning("LinkedIn token expired for post %d", post['id'])
                                update_scheduled_post_status(
                                    post['id'],
                                    status='failed',
                                    error_message='LinkedIn token expired',
                                )
                                continue
                        
                        linkedin_client = get_linkedin_client()
                        result = linkedin_client.create_smart_post(
                            access_token=linkedin_token['access_token'],
                            author_urn=linkedin_token['user_urn'],
                            text=content[:3000],
                            article_title=article_topic,
                        )
                        
                        if result['success']:
                            update_scheduled_post_status(
                                post['id'],
                                status='posted',
                                linkedin_post_urn=result.get('post_urn'),
                            )
                            if post['social_post_id']:
                                mark_social_post_used(post['social_post_id'], True)
                            app.logger.info("Scheduled LinkedIn post %d published successfully", post['id'])
                        else:
                            error_msg = str(result.get('error', 'Unknown error'))[:500]
                            update_scheduled_post_status(
                                post['id'],
                                status='failed',
                                error_message=error_msg,
                            )
                            app.logger.error("Scheduled LinkedIn post %d failed: %s", post['id'], error_msg)
                        
                except Exception as e:
                    app.logger.exception("Error processing scheduled post %d", post['id'])
                    update_scheduled_post_status(
                        post['id'],
                        status='failed',
                        error_message=str(e)[:500],
                    )
                    
        except Exception as e:
            app.logger.exception("Error in scheduled post worker")


# Start the scheduled post worker thread
scheduled_worker_thread = threading.Thread(target=scheduled_post_worker, daemon=True)
scheduled_worker_thread.start()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
