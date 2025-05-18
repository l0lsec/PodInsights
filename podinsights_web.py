from __future__ import annotations
import os
import tempfile
from flask import Flask, request, render_template, redirect, url_for
import feedparser
import requests
from database import (
    init_db,
    get_episode,
    save_episode,
    add_feed,
    list_feeds,
    get_feed_by_id,
    add_ticket,
    list_tickets,
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
        }
        desc = entry.get('summary') or entry.get('description', '')
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
            'image': img,
            'enclosure': url,
            'status': status,
        })
    return render_template('feed.html', feed=feed, episodes=episodes)

@app.route('/process')
def process_episode():
    audio_url = request.args.get('url')
    title = request.args.get('title', 'Episode')
    feed_id = request.args.get('feed_id', type=int)
    if not audio_url:
        return redirect(url_for('index'))
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
        transcript = transcribe_audio(audio_path)
        summary = summarize_text(transcript)
        actions = extract_action_items(transcript)
        out_path = os.path.join(tmpdir, 'results.json')
        write_results_json(transcript, summary, actions, out_path)
        save_episode(audio_url, title, transcript, summary, actions, feed_id)
    return render_template(
        'result.html',
        title=title,
        summary=summary,
        actions=actions,
        feed_id=feed_id,
        url=audio_url,
        tickets=[],
    )


@app.route('/create_jira', methods=['POST'])
def create_jira():
    """Create JIRA tickets for the selected action items."""
    items = request.form.getlist('items')
    episode_url = request.form.get('episode_url')
    if not items:
        return redirect(request.referrer or url_for('index'))
    episode = get_episode(episode_url) if episode_url else None
    episode_id = episode['id'] if episode else None
    created = []
    for item in items:
        try:
            issue = create_jira_issue(item, item)
            key = issue.get('key', '')
            ticket_url = f"{os.environ.get('JIRA_BASE_URL')}/browse/{key}" if key else ''
            if episode_id is not None and key:
                add_ticket(episode_id, item, key, ticket_url)
            created.append({'key': key, 'url': ticket_url})
        except Exception as exc:  # pragma: no cover - external call
            created.append({'error': str(exc)})
    return render_template('jira_result.html', created=created)


@app.route('/tickets')
def view_tickets():
    """Display all created JIRA tickets."""
    tickets = list_tickets()
    return render_template('tickets.html', tickets=tickets)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
