import shutil
import subprocess
import os
import re
from collections import Counter
from typing import List


def transcribe_audio(audio_path: str) -> str:
    """Transcribe an audio file using the `whisper` command if available.

    Parameters
    ----------
    audio_path: str
        Path to the audio file.

    Returns
    -------
    str
        The transcribed text.
    """
    if shutil.which("whisper"):
        result_path = audio_path + "_transcription.txt"
        subprocess.run(["whisper", audio_path, "--output", result_path], check=True)
        with open(result_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        raise NotImplementedError(
            "Audio transcription requires the `whisper` command line tool."
        )


def _tokenize_sentences(text: str) -> List[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s for s in sentences if s]


def summarize_text(text: str, n_sentences: int = 3) -> str:
    """Summarize the text using a simple frequency-based algorithm."""
    sentences = _tokenize_sentences(text)
    if not sentences:
        return ""

    words = re.findall(r"\w+", text.lower())
    freqs = Counter(words)
    sentence_scores = {
        s: sum(freqs[w] for w in re.findall(r"\w+", s.lower())) for s in sentences
    }
    top_sentences = sorted(sentence_scores, key=sentence_scores.get, reverse=True)[
        :n_sentences
    ]
    return " " .join(top_sentences)


def extract_action_items(text: str) -> List[str]:
    """Extract simple action items from the text.

    Sentences containing words like 'should', 'must', or phrases like 'need to'
    are treated as potential action items.
    """
    sentences = _tokenize_sentences(text)
    keywords = ["should", "must", "need to", "let's", "remember to"]
    actions = [
        s
        for s in sentences
        if any(k in s.lower() for k in keywords)
    ]
    return actions


def main(audio_path: str) -> None:
    try:
        transcript = transcribe_audio(audio_path)
    except NotImplementedError as e:
        print(e)
        return

    summary = summarize_text(transcript)
    actions = extract_action_items(transcript)

    print("Summary:\n", summary)
    print("\nAction Items:")
    for item in actions:
        print("-", item)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Transcribe and analyze podcasts")
    parser.add_argument("audio", help="Path to the podcast audio file")
    args = parser.parse_args()
    main(args.audio)
