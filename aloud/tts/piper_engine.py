"""PRIMARY TTS engine: piper-tts.

API confirmed by running it against piper-tts 1.6.0 (see docs/DECISIONS.md):
voice.synthesize(text, syn_config=SynthesisConfig(length_scale=...)) yields
AudioChunk objects with an .audio_int16_bytes property; length_scale < 1.0
is faster, > 1.0 is slower.
"""

import os
from pathlib import Path

from aloud.errors import EngineUnavailableError, ModelNotFoundError
from aloud.paths import models_dir
from aloud.tts.base import Engine

_DOWNLOAD_HINT = (
    "Download one first with 'python -m aloud.voices download "
    "en_US-lessac-medium' (voices are hosted at rhasspy/piper-voices "
    "on Hugging Face)."
)


def _resolve_model_path(model_path=None):
    if model_path is None:
        model_path = os.environ.get("ALOUD_VOICE_MODEL")
    if model_path is None:
        model_path = models_dir()

    candidate = Path(model_path)
    if candidate.is_dir():
        onnx_files = sorted(candidate.glob("*.onnx"))
        if not onnx_files:
            raise ModelNotFoundError(
                f"No Piper voice model (.onnx) found in {candidate}. {_DOWNLOAD_HINT}"
            )
        return onnx_files[0]

    if not candidate.exists():
        raise ModelNotFoundError(
            f"Piper voice model not found: {candidate}. {_DOWNLOAD_HINT}"
        )
    return candidate


class PiperEngine(Engine):
    name = "piper"

    def __init__(self, model_path=None):
        try:
            import piper.voice  # noqa: F401
        except ImportError as exc:
            raise EngineUnavailableError("piper-tts is not installed") from exc

        self._model_path = _resolve_model_path(model_path)
        self._voice = None
        self._sample_rate = None

    def _ensure_loaded(self):
        if self._voice is not None:
            return
        from piper.voice import PiperVoice

        self._voice = PiperVoice.load(str(self._model_path))
        self._sample_rate = self._voice.config.sample_rate

    @property
    def sample_rate(self) -> int:
        self._ensure_loaded()
        return self._sample_rate

    def synth(self, text: str, speed: float = 1.0) -> bytes:
        self._ensure_loaded()
        from piper.config import SynthesisConfig

        length_scale = (1.0 / speed) if speed else 1.0
        syn_config = SynthesisConfig(length_scale=length_scale)

        pcm = bytearray()
        for audio_chunk in self._voice.synthesize(text, syn_config=syn_config):
            pcm.extend(audio_chunk.audio_int16_bytes)
        return bytes(pcm)

    def close(self):
        self._voice = None
