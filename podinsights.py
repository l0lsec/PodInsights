import os
import json
import logging
from typing import List

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-40-mini")

logger = logging.getLogger(__name__)


def configure_logging(verbose: bool = False) -> None:
    """Configure application logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s")
    logger.debug("Logging configured. Level=%s", logging.getLevelName(level))


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
    logger.debug("Starting transcription of %s", audio_path)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - optional dependency
        logger.exception("faster-whisper not installed")
        raise NotImplementedError(
            "Audio transcription requires the `faster-whisper` package."
        ) from exc

    try:
        model = WhisperModel("base", device="cpu")
        segments, _ = model.transcribe(audio_path)
        segments_list = list(segments)  # Convert generator to list
        transcript = " ".join(segment.text.strip() for segment in segments_list)
        logger.debug("Transcription finished with %d segments", len(segments_list))
        return transcript
    except Exception as exc:
        logger.exception("Transcription failed")
        raise RuntimeError("Failed to transcribe audio") from exc


def summarize_text(text: str) -> str:
    """Summarize ``text`` using OpenAI."""

    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        if not client.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        logger.debug("Requesting summary from OpenAI")
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": f"Summarize the following text:\n{text}"}],
            temperature=0.2,
        )
        summary = response.choices[0].message.content.strip()
        logger.debug("Summary received")
        return summary
    except Exception as exc:
        logger.exception("OpenAI summarization failed")
        raise RuntimeError("Failed to summarize text with OpenAI") from exc


def extract_action_items(text: str) -> List[str]:
    """Extract action items from ``text`` using OpenAI."""

    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        if not client.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        logger.debug("Requesting action items from OpenAI")
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Extract a concise list of action items from the "
                        "following text. Respond with one item per line "
                        "and no additional commentary.\n" + text
                    ),
                }
            ],
            temperature=0.2,
        )
        lines = response.choices[0].message.content.splitlines()
        actions = [ln.lstrip("- ").strip() for ln in lines if ln.strip()]
        logger.debug("Action items received: %d", len(actions))
        return actions
    except Exception as exc:
        logger.exception("OpenAI action item extraction failed")
        raise RuntimeError("Failed to extract action items with OpenAI") from exc


def write_results_json(transcript: str, summary: str, actions: List[str], output_path: str) -> None:
    """Write the analysis results to ``output_path`` as JSON."""

    data = {
        "transcript": transcript,
        "summary": summary,
        "action_items": actions,
    }
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug("Results written to %s", output_path)
    except Exception as exc:
        logger.exception("Failed to write results JSON")
        raise RuntimeError("Could not write results JSON") from exc


def main(audio_path: str, json_path: str | None = None, verbose: bool = False) -> None:
    configure_logging(verbose)

    try:
        transcript = transcribe_audio(audio_path)
        summary = summarize_text(transcript)
        actions = extract_action_items(transcript)

        logger.info("Summary:\n%s", summary)
        logger.info("Action Items:")
        for item in actions:
            logger.info("- %s", item)

        if json_path is None:
            json_path = os.path.splitext(audio_path)[0] + ".json"
        write_results_json(transcript, summary, actions, json_path)
        logger.info("Results written to %s", json_path)
    except Exception as exc:
        logger.exception("Processing failed")


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
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )
    args = parser.parse_args()
    main(args.audio, args.json, args.verbose)
