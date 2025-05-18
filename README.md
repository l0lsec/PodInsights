**PodInsights: AI-Powered Podcast Transcription, Summarization & Action Item Extraction Tool**

PodInsights is the ultimate podcast analysis tool for creators, researchers, and business professionals who need to extract valuable insights from audio content. Our tool combines state-of-the-art speech recognition with powerful AI summarization to transform hours of podcast content into actionable intelligence.

### Key Features

- **Accurate Podcast Transcription** - Convert any podcast episode into searchable text with industry-leading accuracy
- **Intelligent Summarization** - Automatically generate concise summaries capturing the core message of any episode
- **Action Item Extraction** - Never miss important tasks or follow-ups mentioned in podcast discussions
- **JIRA Integration** - Seamlessly create tickets from extracted action items for project management
- **RSS Feed Support** - Process entire podcast feeds directly from their source

### Use Cases

- Content creators tracking competitive analysis from industry podcasts
- Researchers analyzing interview content for qualitative research
- Business professionals extracting action items from recorded meetings
- Podcast fans who want to quickly understand episode content before listening
- Knowledge workers converting audio information into structured text data

*PodInsights - Transforming podcast content into actionable intelligence.*

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

Navigate to `http://localhost:5001` and add an RSS feed URL. Stored feeds are listed on the home page so you can return to them later. Selecting a feed shows the episodes along with their processing status.

Episode descriptions and any images provided by the feed are displayed next to each title to help you identify episodes before processing.

You can listen to any episode directly from the browser. Each episode row now includes a small audio player so you can preview the content before processing or reviewing tickets.

Processed episodes are stored in a local SQLite database (`episodes.db`). Each episode records the transcript, summary, and action items. The feed view reports whether these pieces of information have been extracted.


### Creating JIRA Tickets

You can create JIRA issues directly from the action items listed on the result
page. Set the following environment variables so the web app can access your
JIRA Cloud instance:

- `JIRA_BASE_URL` – e.g. `https://example.atlassian.net`
- `JIRA_EMAIL` – the email associated with an API token
- `JIRA_API_TOKEN` – your JIRA API token
- `JIRA_PROJECT_KEY` – project key for new issues

Select the action items you want to track and click **Create JIRA Tickets**.
Created issues are stored locally so you can access them later. When viewing an
episode's results the associated JIRA tickets are listed with links. A dedicated
"Tickets" page in the web interface lists every issue that has been created.

## Credits

Developed by Sedric "Show Up Show Out" Louissaint from Show Up Show Out Security. 

Learn more about Show Up Show Out Security at [susos.co](https://susos.co).

---