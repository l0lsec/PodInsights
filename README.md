# PodInsights

PodInsights is a simple command-line tool that helps you transcribe podcast audio files and extract useful information from them. The current implementation relies on the [`faster-whisper`](https://github.com/guillaumekln/faster-whisper) library for transcription. It then creates a short summary and extracts potential action items from the transcript using basic text processing.

## Requirements

- Python 3.11+
- [`faster-whisper`](https://github.com/guillaumekln/faster-whisper) installed for audio transcription

## Usage

```bash
python podinsights.py path/to/podcast.mp3
```

The script will attempt to transcribe the audio file using `faster-whisper`, generate a short summary of the conversation, and list action items detected in the transcript. It also writes these results to a JSON file next to the audio by default. You can specify a custom output path with the `--json` option.

> **Note**: If the `faster-whisper` package is not installed, the script will raise a `NotImplementedError`. You can install it via `pip install faster-whisper` if you have internet access.

The JSON file contains three fields:

- `transcript` – the full transcript of the audio
- `summary` – the generated summary text
- `action_items` – a list of extracted action items
