"""get_engine(name) registry."""

from aloud.errors import EngineUnavailableError
from aloud.tts.chunker import chunk_text
from aloud.tts.piper_engine import PiperEngine
from aloud.tts.pyttsx3_engine import Pyttsx3Engine


def get_engine(name="auto", model_path=None):
    if name == "piper":
        return PiperEngine(model_path=model_path)
    if name == "pyttsx3":
        return Pyttsx3Engine()
    if name != "auto":
        raise EngineUnavailableError(f"Unknown TTS engine: {name}")

    # "auto": Piper is primary. Only fall back to pyttsx3 if Piper itself
    # is unavailable (not importable) - a missing voice model must surface
    # as ModelNotFoundError, never silently downgrade the voice.
    try:
        return PiperEngine(model_path=model_path)
    except EngineUnavailableError:
        return Pyttsx3Engine()


__all__ = ["get_engine", "chunk_text"]
