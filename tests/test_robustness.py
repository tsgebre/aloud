"""Tests for the robustness fixes: abbreviation-aware sentence chunking,
empty-owner-password PDFs, SynchronizedEngine serialization, and the web
export temp-file cleanup."""

import pathlib
import threading
import time
import types

import pytest

from aloud.errors import EncryptedPdfError
from aloud.extract import extract_text
from aloud.tts.base import SynchronizedEngine
from aloud.tts.chunker import chunk_text

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"


# --- chunker: abbreviations must not split sentences ------------------------


def test_chunker_still_splits_real_sentence_boundaries():
    assert chunk_text("First sentence. Second sentence.") == [
        "First sentence.",
        "Second sentence.",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Dr. Smith went home early.",
        "They met at 3 p.m. near the station.",
        "See e.g. the appendix for details.",
        "J. R. R. Tolkien wrote it.",
        "Miller et al. found the same result.",
        "The U.S. economy grew this year.",
    ],
)
def test_chunker_does_not_split_after_abbreviations(text):
    assert chunk_text(text) == [text]


def test_chunker_abbreviation_then_real_boundary():
    assert chunk_text("Ask Dr. Smith about it. Then decide.") == [
        "Ask Dr. Smith about it.",
        "Then decide.",
    ]


def test_chunker_merged_long_sentence_still_hard_wraps():
    long_text = "Dr. Smith said " + "word " * 120 + "the end."
    chunks = chunk_text(long_text, max_chars=100)
    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks) == " ".join(long_text.split())


# --- PDF: empty user password is readable, real password is not -------------


def _encrypted_copy(tmp_path, name, user_password, owner_password):
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(SAMPLES / "sample.pdf").pages:
        writer.add_page(page)
    writer.encrypt(user_password=user_password, owner_password=owner_password)
    out = tmp_path / name
    with open(out, "wb") as f:
        writer.write(f)
    return out


def test_pdf_with_only_owner_password_is_extracted(tmp_path):
    pdf = _encrypted_copy(tmp_path, "owner-only.pdf", user_password="", owner_password="secret")
    document = extract_text(pdf)
    assert "Aloud reads PDF documents aloud." in document.full_text


def test_pdf_with_user_password_is_rejected(tmp_path):
    pdf = _encrypted_copy(tmp_path, "locked.pdf", user_password="hunter2", owner_password="secret")
    with pytest.raises(EncryptedPdfError):
        extract_text(pdf)


# --- SynchronizedEngine: no concurrent entry into the wrapped engine --------


class _ConcurrencyProbeEngine:
    name = "probe"

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._probe_lock = threading.Lock()

    @property
    def sample_rate(self):
        return 22050

    def synth(self, text, speed=1.0):
        with self._probe_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.02)  # widen the race window
        with self._probe_lock:
            self.active -= 1
        return b"\x00\x00"

    def close(self):
        pass


def test_synchronized_engine_serializes_synth_calls():
    probe = _ConcurrencyProbeEngine()
    engine = SynchronizedEngine(probe)
    threads = [
        threading.Thread(target=engine.synth, args=(f"chunk {i}",)) for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert probe.max_active == 1
    assert engine.name == "probe"
    assert engine.sample_rate == 22050


def test_gui_engine_is_synchronized():
    # The Tk GUI shares one engine between the playback thread and the
    # export worker thread; it must come back wrapped.
    import aloud.gui as gui_module

    assert gui_module.SynchronizedEngine is SynchronizedEngine


# --- web: abandoned finished export must not leak its temp file -------------


def test_web_export_replacing_finished_job_removes_stale_file(tmp_path, monkeypatch):
    from aloud import web as web_module

    state = web_module.WebState(models_dir=tmp_path)
    state.doc_name = "doc.txt"
    state.doc_full_text = "Hello there."

    fake_engine = types.SimpleNamespace(name="fake")
    monkeypatch.setattr(web_module, "get_engine", lambda name, model_path=None: fake_engine)

    def fake_synthesize_to_wav(text, out_path, engine, speed=1.0, progress=None):
        pathlib.Path(out_path).write_bytes(b"RIFFfake")
        return {"chunks": 1, "duration_sec": 0.1, "sample_rate": 22050}

    monkeypatch.setattr(web_module, "synthesize_to_wav", fake_synthesize_to_wav)

    # A finished export whose file the client never downloaded:
    stale = tmp_path / "stale-export.wav"
    stale.write_bytes(b"RIFFold")
    state.export_job = {
        "state": "done", "index": 1, "total": 1, "path": str(stale),
        "fmt": "wav", "error": None, "result": {},
    }

    assert state.start_export("wav", "", 1.0) is True
    for _ in range(100):
        with state.lock:
            if state.export_job["state"] != "running":
                break
        time.sleep(0.02)
    assert state.export_job["state"] == "done"
    assert not stale.exists(), "stale export file should have been cleaned up"
    # The new export's own file exists and is tracked:
    assert pathlib.Path(state.export_job["path"]).exists()
    pathlib.Path(state.export_job["path"]).unlink()
