from __future__ import annotations
import os
import tempfile
import threading
from queue import Queue
from flask import Flask, request, render_template, redirect, url_for
import re
import feedparser
import requests
from html import unescape
from database import (
    init_db,
    get_episode,
    save_episode,
    queue_episode,
    update_episode_status,
    add_feed,
    list_feeds,
    get_feed_by_id,
    add_ticket,
    list_tickets,
    list_all_episodes,
)
from podinsights import (
    transcribe_audio,
    summarize_text,
    extract_action_items,
    write_results_json,
    configure_logging,
)

app = Flask(__name__)
configure_logging()
init_db()

# Background processing queue
task_queue: Queue = Queue()


def worker() -> None:
    """Background thread processing queued episodes."""
    while True:
        item = task_queue.get()
        if item is None:
            break
        url = item["url"]
        title = item.get("title", "Episode")
        feed_id = item.get("feed_id")
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
                save_episode(url, title, transcript, summary, actions, feed_id)
        except Exception:
            app.logger.exception("Failed to process episode %s", url)
            update_episode_status(url, "error")
        finally:
            task_queue.task_done()


worker_thread = threading.Thread(target=worker, daemon=True)
worker_thread.start()


def strip_html(text: str) -> str:
    """Return plain text with HTML tags removed."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def make_short_description(text: str, limit: int = 200) -> str:
    """Return a short preview from the provided text."""
    if not text:
        return ""
    text = text.strip()
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
    resp = requests.post(url, json=data, auth=(email, token))
    resp.raise_for_status()
    return resp.json()

# Templates are stored in the ``templates`` directory

@app.route('/', methods=['GET', 'POST'])
def index():
    feeds = list_feeds()
    if request.method == 'POST':
        feed_url = request.form['feed_url']
        feed = feedparser.parse(feed_url)
        title = feed.feed.get('title', feed_url)
        feed_id = add_feed(feed_url, title)
        return redirect(url_for('view_feed', feed_id=feed_id))
    return render_template('feeds.html', feeds=feeds)


@app.route('/feed/<int:feed_id>')
def view_feed(feed_id: int):
    feed = get_feed_by_id(feed_id)
    if not feed:
        return redirect(url_for('index'))
    feed_data = feedparser.parse(feed['url'])
    episodes = []
    for entry in feed_data.entries:
        if not entry.get('enclosures'):
            continue
        url = entry.enclosures[0].href
        ep_db = get_episode(url)
        status = {
            'transcribed': ep_db is not None and bool(ep_db['transcript']),
            'summarized': ep_db is not None and bool(ep_db['summary']),
            'actions': ep_db is not None and bool(ep_db['action_items']),
            'state': ep_db['status'] if ep_db else 'new',
        }
        desc = entry.get('summary') or entry.get('description', '')
        clean_desc = strip_html(desc)
        img = None
        if hasattr(entry, 'image') and getattr(entry.image, 'href', None):
            img = entry.image.href
        elif entry.get('itunes_image'):
            img = entry.itunes_image.get('href') if isinstance(entry.itunes_image, dict) else entry.itunes_image
        elif entry.get('media_thumbnail'):
            img = entry.media_thumbnail[0].get('url')
        elif entry.get('media_content'):
            img = entry.media_content[0].get('url')
        episodes.append({
            'title': entry.title,
            'description': desc,
            'clean_description': clean_desc,
            'short_description': make_short_description(clean_desc),
            'image': img,
            'enclosure': url,
            'status': status,
        })
    return render_template('feed.html', feed=feed, episodes=episodes)


@app.route('/enqueue')
def enqueue_episode():
    """Queue an episode for background processing."""
    audio_url = request.args.get('url')
    title = request.args.get('title', 'Episode')
    feed_id = request.args.get('feed_id', type=int)
    if not audio_url or feed_id is None:
        return redirect(url_for('index'))
    queue_episode(audio_url, title, feed_id)
    task_queue.put({'url': audio_url, 'title': title, 'feed_id': feed_id})
    return redirect(url_for('status_page'))

@app.route('/process')
def process_episode():
    audio_url = request.args.get('url')
    title = request.args.get('title', 'Episode')
    feed_id = request.args.get('feed_id', type=int)
    description = strip_html(request.args.get('description', ''))
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
    existing = get_episode(audio_url)
    if existing:
        summary = existing["summary"]
        actions = existing["action_items"].splitlines()
        tickets = list_tickets(existing["id"])
        return render_template(
            'result.html',
            title=existing["title"],
            summary=summary,
            actions=actions,
            description=description,
            feed_id=feed_id,
            url=audio_url,
            tickets=tickets,
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, 'episode.mp3')
        with requests.get(audio_url, stream=True) as r:
            r.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
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
        save_episode(audio_url, title, transcript, summary, actions, feed_id)
    return render_template(
        'result.html',
        title=title,
        summary=summary,
        actions=actions,
        description=description,
        feed_id=feed_id,
        url=audio_url,
        tickets=[],
    )


@app.route('/create_jira', methods=['POST'])
def create_jira():
    """Create JIRA tickets for the selected action items."""
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


@app.route('/status')
def status_page():
    """Display processing status for all episodes."""
    episodes = list_all_episodes()
    feeds = {f["id"]: f["title"] for f in list_feeds()}
    return render_template('status.html', episodes=episodes, feeds=feeds)


@app.route('/tickets')
def view_tickets():
    """Display all created JIRA tickets."""
    tickets = list_tickets()
    return render_template('tickets.html', tickets=tickets)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
