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
        episodes.append({'title': entry.title, 'enclosure': url, 'status': status})
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
        return render_template('result.html', title=existing["title"], summary=summary, actions=actions, feed_id=feed_id)

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
    return render_template('result.html', title=title, summary=summary, actions=actions, feed_id=feed_id)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
