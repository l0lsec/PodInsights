from __future__ import annotations
import os
import tempfile
from flask import Flask, request, render_template_string, redirect, url_for
import feedparser
import requests
from podinsights import (
    transcribe_audio,
    summarize_text,
    extract_action_items,
    write_results_json,
    configure_logging,
)

app = Flask(__name__)
configure_logging()

INDEX_TEMPLATE = """
<!doctype html>
<title>PodInsights</title>
<h1>PodInsights Feed Processor</h1>
<form method=post>
  RSS Feed URL:<br>
  <input type=text name=feed_url size=60>
  <input type=submit value=Load>
</form>
{% if episodes %}
  <h2>Episodes</h2>
  <ul>
  {% for ep in episodes %}
    <li>{{ ep.title }} - <a href="{{ url_for('process_episode', url=ep.enclosure, title=ep.title) }}">Process</a></li>
  {% endfor %}
  </ul>
{% endif %}
"""

RESULT_TEMPLATE = """
<!doctype html>
<title>PodInsights Result</title>
<h1>{{ title }}</h1>
<h2>Summary</h2>
<pre>{{ summary }}</pre>
<h2>Action Items</h2>
<ul>
{% for item in actions %}
  <li>{{ item }}</li>
{% endfor %}
</ul>
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    episodes = None
    if request.method == 'POST':
        feed_url = request.form['feed_url']
        feed = feedparser.parse(feed_url)
        episodes = [
            {
                'title': entry.title,
                'enclosure': entry.enclosures[0].href if entry.enclosures else None,
            }
            for entry in feed.entries
            if entry.get('enclosures')
        ]
    return render_template_string(INDEX_TEMPLATE, episodes=episodes)

@app.route('/process')
def process_episode():
    audio_url = request.args.get('url')
    title = request.args.get('title', 'Episode')
    if not audio_url:
        return redirect(url_for('index'))
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
        # Read the json results to display
        with open(out_path, 'r', encoding='utf-8') as f:
            data = f.read()
    return render_template_string(RESULT_TEMPLATE, title=title, summary=summary, actions=actions)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
