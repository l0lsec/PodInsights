# PodInsights

PodInsights is a simple command-line tool that helps you transcribe podcast audio files and extract useful information from them. The current implementation relies on the [`faster-whisper`](https://github.com/guillaumekln/faster-whisper) library for transcription. Summarization and action item extraction are performed exclusively using OpenAI's chat models.

## Requirements

- Python 3.11+
- [`faster-whisper`](https://github.com/guillaumekln/faster-whisper) installed for audio transcription
- [`openai`](https://pypi.org/project/openai/) and a valid `OPENAI_API_KEY` environment variable
- Optional for the web interface: [`Flask`](https://palletsprojects.com/p/flask/), [`feedparser`](https://pypi.org/project/feedparser/), and [`requests`](https://pypi.org/project/requests/)
  (`sqlite3` from the standard library is used for episode tracking)

## Usage (CLI)

```bash
python podinsights.py path/to/podcast.mp3
```

The script will attempt to transcribe the audio file using `faster-whisper`, then ask OpenAI to produce a short summary and extract action items. Results are also written to a JSON file next to the audio by default. You can specify a custom output path with the `--json` option. Use `--verbose` to enable debug logging.

> **Note**: If the `faster-whisper` package is not installed, the script will raise a `NotImplementedError`. You can install it via `pip install faster-whisper` if you have internet access.
> **Note**: Summarization and action item extraction require OpenAI access. Ensure `OPENAI_API_KEY` is set in your environment.

The JSON file contains three fields:

- `transcript` – the full transcript of the audio
- `summary` – the generated summary text
- `action_items` – a list of extracted action items

## Usage (Web UI)

A lightweight Flask application is provided in `podinsights_web.py`. It allows you to enter a podcast RSS feed URL, list the available episodes, and process an episode directly from your browser. Install the extra dependencies and run the app:

```bash
pip install flask feedparser requests
python podinsights_web.py
```

Navigate to `http://localhost:5000` and add an RSS feed URL. Stored feeds are listed on the home page so you can return to them later. Selecting a feed shows the episodes along with their processing status.

Processed episodes are stored in a local SQLite database (`episodes.db`). Each episode records the transcript, summary, and action items. The feed view reports whether these pieces of information have been extracted.


### Improved Web UI

The web interface now loads the [Typeset](https://github.com/dobtco/typeset) CSS library via CDN, providing a cleaner and more modern layout.
No extra configuration is necessary.
