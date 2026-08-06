"""OPTIONAL fallback TTS engine: pyttsx3 (requires system espeak-ng).

Never selected automatically unless Piper is unavailable (see
aloud/tts/__init__.py). Availability is capability-checked, not assumed:
on this development machine espeak-ng is absent, so is_available()
returns False and construction raises EngineUnavailableError.
"""

import tempfile
import wave
from pathlib import Path

from aloud.errors import EngineUnavailableError
from aloud.tts.base import Engine

_UNAVAILABLE_MESSAGE = (
    "pyttsx3 fallback is unavailable: the system 'espeak-ng' (or 'espeak') "
    "package is not installed. Install it via your OS package manager, "
    "e.g. 'sudo apt install espeak-ng' or 'sudo dnf install espeak-ng'."
)


def is_available() -> bool:
    try:
        import pyttsx3
    except ImportError:
        return False

    try:
        engine = pyttsx3.init()
    except Exception:
        return False

    try:
        engine.stop()
    except Exception:
        pass
    return True


class Pyttsx3Engine(Engine):
    name = "pyttsx3"

    def __init__(self):
        if not is_available():
            raise EngineUnavailableError(_UNAVAILABLE_MESSAGE)

        import pyttsx3

        self._engine = pyttsx3.init()
        self._sample_rate = None

    @property
    def sample_rate(self) -> int:
        if self._sample_rate is None:
            self.synth(".", speed=1.0)
        return self._sample_rate

    def synth(self, text: str, speed: float = 1.0) -> bytes:
        base_rate = self._engine.getProperty("rate")
        self._engine.setProperty("rate", base_rate * speed)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_path = Path(tmp_dir) / "chunk.wav"
                self._engine.save_to_file(text, str(out_path))
                self._engine.runAndWait()
                with wave.open(str(out_path), "rb") as wav_file:
                    self._sample_rate = wav_file.getframerate()
                    pcm = wav_file.readframes(wav_file.getnframes())
        finally:
            self._engine.setProperty("rate", base_rate)
        return pcm

    def close(self):
        try:
            self._engine.stop()
        except Exception:
            pass
