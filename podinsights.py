import os
import re
import json
from collections import Counter
from typing import List


def transcribe_audio(audio_path: str) -> str:
    """Transcribe an audio file using the ``faster-whisper`` library.

    Parameters
    ----------
    audio_path: str
        Path to the audio file.

    Returns
    -------
    str
        The transcribed text.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise NotImplementedError(
            "Audio transcription requires the `faster-whisper` package."
        ) from exc

    # Use the small model for a reasonable default
    model = WhisperModel("base", device="cpu")
    segments, _ = model.transcribe(audio_path)
    transcript = " ".join(segment.text.strip() for segment in segments)
    return transcript


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


def write_results_json(transcript: str, summary: str, actions: List[str], output_path: str) -> None:
    """Write the analysis results to ``output_path`` as JSON."""

    data = {
        "transcript": transcript,
        "summary": summary,
        "action_items": actions,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main(audio_path: str, json_path: str | None = None) -> None:
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

    if json_path is None:
        json_path = os.path.splitext(audio_path)[0] + ".json"
    write_results_json(transcript, summary, actions, json_path)
    print(f"\nResults written to {json_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Transcribe and analyze podcasts")
    parser.add_argument("audio", help="Path to the podcast audio file")
    parser.add_argument(
        "-j",
        "--json",
        help=(
            "Optional path to save results as JSON; defaults to <audio>.json"
        ),
    )
    args = parser.parse_args()
    main(args.audio, args.json)
