"""Transcribe audio interview recordings to text.

Two backends are supported, tried in this order:
  1. OpenAI Whisper API   - used when OPENAI_API_KEY is set. Fast, no local
                             model download, costs per minute of audio.
  2. Local faster-whisper - used when the `faster-whisper` package is
                             installed. Runs on-device, no API key needed,
                             but requires a one-time model download and more
                             compute.
If neither is available, a clear error is raised telling the user how to
enable one of the two paths (or to submit a text transcript instead).
"""
from pathlib import Path

from config import Config


class TranscriptionError(Exception):
    pass


def transcribe_audio(path: Path) -> str:
    if Config.OPENAI_API_KEY:
        return _transcribe_openai(path)
    if _faster_whisper_available():
        return _transcribe_local(path)
    raise TranscriptionError(
        "No transcription backend is configured. Either set OPENAI_API_KEY "
        "in .env to use the Whisper API, or install the local fallback with "
        "`pip install faster-whisper` (no API key needed). Alternatively, "
        "upload a text transcript instead of audio."
    )


def _transcribe_openai(path: Path) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise TranscriptionError(
            "The `openai` package is required for Whisper API transcription. "
            "Install it with `pip install openai`."
        ) from e

    client = OpenAI(api_key=Config.OPENAI_API_KEY)
    with open(path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    return result.text


def _faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


_local_model = None


def _transcribe_local(path: Path) -> str:
    global _local_model
    from faster_whisper import WhisperModel

    if _local_model is None:
        _local_model = WhisperModel(Config.LOCAL_WHISPER_MODEL, compute_type="int8")

    segments, _info = _local_model.transcribe(str(path))
    return " ".join(segment.text.strip() for segment in segments)
