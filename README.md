# PodInsights

PodInsights is a simple command-line tool that helps you transcribe podcast audio files and extract useful information from them. The current implementation relies on the [`whisper`](https://github.com/openai/whisper) command line tool for transcription. It then creates a short summary and extracts potential action items from the transcript using basic text processing.

## Requirements

- Python 3.11+
- [`whisper`](https://github.com/openai/whisper) installed and available on your PATH for audio transcription

## Usage

```bash
python podinsights.py path/to/podcast.mp3
```

The script will attempt to transcribe the audio file using `whisper`, generate a short summary of the conversation, and list action items detected in the transcript.

> **Note**: If the `whisper` tool is not installed, the script will raise a `NotImplementedError`. You can install whisper from source or via `pip install git+https://github.com/openai/whisper.git` if you have internet access.
