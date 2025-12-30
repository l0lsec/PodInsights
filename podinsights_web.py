"""Flask web interface for the PodInsights tool."""

from __future__ import annotations
import os
import tempfile
import threading
from queue import Queue
from flask import Flask, request, render_template, redirect, url_for
import re
import feedparser
import requests
from datetime import datetime
import time
from html import unescape
from database import (
    init_db,
    get_episode,
    get_episode_by_id,
    save_episode,
    queue_episode,
    update_episode_status,
    delete_episode_by_id,
    reset_episode_for_reprocess,
    add_feed,
    list_feeds,
    get_feed_by_id,
    delete_feed,
    add_ticket,
    list_tickets,
    list_all_episodes,
    add_article,
    get_article,
    list_articles,
    update_feed_metadata,
)
from podinsights import (
    transcribe_audio,
    summarize_text,
    extract_action_items,
    write_results_json,
    configure_logging,
    generate_article,
)

app = Flask(__name__)
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

    # Fetch content from the feed
    if feed_id:
        feed = get_feed_by_id(feed_id)
        if feed:
            feed_data = feedparser.parse(feed['url'])
            for entry in feed_data.entries:
                entry_url = entry.get('link', entry.get('id', ''))
                if entry_url == article_url:
                    desc = entry.get('summary') or entry.get('description', '')
                    description = strip_html(desc)
                    # Get full content
                    if hasattr(entry, 'content') and entry.content:
                        content = entry.content[0].get('value', '')
                    if not content:
                        content = desc
                    content = strip_html(content)
                    break

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
    created = []
    for item in items:
        try:
            description = (
                f"Action item: {item}\n\n"
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
    return render_template('article.html', article=dict(article))


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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
