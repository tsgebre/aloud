import pathlib

import pytest

from aloud.errors import EngineUnavailableError, ModelNotFoundError
from aloud.tts import get_engine
from aloud.tts.chunker import chunk_text
from aloud.tts.piper_engine import PiperEngine
from aloud.tts.pyttsx3_engine import Pyttsx3Engine
from aloud.tts.pyttsx3_engine import is_available as pyttsx3_is_available
from aloud.tts.writer import synthesize_to_wav

MODELS_DIR = pathlib.Path(__file__).resolve().parent.parent / "models"
REAL_MODEL = next(iter(sorted(MODELS_DIR.glob("*.onnx"))), None)

requires_real_model = pytest.mark.skipif(
    REAL_MODEL is None, reason="no Piper voice model present under models/"
)


# --- chunker ---------------------------------------------------------------


def test_chunk_text_splits_paragraphs():
    text = "Para one sentence.\n\nPara two sentence."
    assert chunk_text(text) == ["Para one sentence.", "Para two sentence."]


def test_chunk_text_splits_sentences():
    text = "First sentence. Second sentence! Third sentence?"
    assert chunk_text(text) == [
        "First sentence.",
        "Second sentence!",
        "Third sentence?",
    ]


def test_chunk_text_hard_wraps_long_sentences():
    long_run = " ".join(["word"] * 200)  # no sentence punctuation at all
    chunks = chunk_text(long_run, max_chars=50)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)
    # no words lost or reordered by wrapping
    assert " ".join(chunks).split() == long_run.split()


def test_chunk_text_empty_input_returns_empty_list():
    assert chunk_text("") == []
    assert chunk_text("   \n\n   \t") == []


def test_chunk_text_preserves_repeated_sentences():
    text = "Same sentence. Same sentence. Same sentence."
    chunks = chunk_text(text)
    assert chunks == ["Same sentence."] * 3
    assert len(chunks) == 3


# --- engine resolution -------------------------------------------------------


def test_piper_engine_raises_model_not_found_for_empty_dir(tmp_path):
    with pytest.raises(ModelNotFoundError) as exc_info:
        PiperEngine(model_path=tmp_path)
    assert "\n" not in exc_info.value.user_message


def test_pyttsx3_engine_unavailable_names_espeak_when_unavailable():
    if pyttsx3_is_available():
        pytest.skip("pyttsx3/espeak-ng is available on this machine")
    with pytest.raises(EngineUnavailableError) as exc_info:
        Pyttsx3Engine()
    assert "espeak" in exc_info.value.user_message.lower()
    assert "\n" not in exc_info.value.user_message


def test_get_engine_auto_does_not_downgrade_on_model_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("ALOUD_VOICE_MODEL", str(tmp_path))
    with pytest.raises(ModelNotFoundError):
        get_engine("auto")


# --- writer streaming behaviour (fake engine, no real synthesis needed) ----


class _FakeEngine:
    name = "fake"
    sample_rate = 16000

    def __init__(self):
        self.calls = []

    def synth(self, text, speed=1.0):
        self.calls.append(text)
        n_samples = int(0.1 * self.sample_rate)
        return b"\x00\x00" * n_samples

    def close(self):
        pass


def test_synthesize_to_wav_calls_engine_once_per_chunk(tmp_path):
    text = "First sentence. Second sentence. Third sentence."
    engine = _FakeEngine()
    result = synthesize_to_wav(text, tmp_path / "out.wav", engine)
    expected = len(chunk_text(text))
    assert len(engine.calls) == expected
    assert result["chunks"] == expected
    assert result["sample_rate"] == 16000
    assert result["duration_sec"] > 0


def test_synthesize_to_wav_progress_reaches_total(tmp_path):
    text = "One. Two. Three."
    engine = _FakeEngine()
    progress_calls = []
    synthesize_to_wav(
        text,
        tmp_path / "out.wav",
        engine,
        progress=lambda done, total: progress_calls.append((done, total)),
    )
    total = len(chunk_text(text))
    assert len(progress_calls) == total
    assert progress_calls[-1] == (total, total)


# --- real Piper synthesis (skipped if no voice model is present) -----------


@requires_real_model
def test_real_piper_synthesis_produces_valid_wav(tmp_path):
    import wave

    engine = PiperEngine(model_path=REAL_MODEL)
    out_path = tmp_path / "sample_out.wav"
    result = synthesize_to_wav("Aloud test synthesis.", out_path, engine)

    with wave.open(str(out_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == result["sample_rate"]
        assert wav_file.getnframes() > 0


@requires_real_model
def test_real_piper_speed_direction(tmp_path):
    engine = PiperEngine(model_path=REAL_MODEL)
    text = "Aloud test synthesis. Aloud test synthesis. Aloud test synthesis."

    slow = synthesize_to_wav(text, tmp_path / "slow.wav", engine, speed=1.0)
    fast = synthesize_to_wav(text, tmp_path / "fast.wav", engine, speed=1.5)

    assert fast["duration_sec"] < slow["duration_sec"]
