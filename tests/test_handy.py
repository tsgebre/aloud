"""Tests for the convenience features: CLI stdin/MP3, pasted text via the
web API, export cancellation, and the portable models-dir resolution."""

import io
import json
import pathlib
import threading
import time
import types
import urllib.request

import pytest

from aloud.__main__ import main as cli_main
from aloud.errors import AloudError
from aloud.extract import document_from_text

MODELS_DIR = pathlib.Path(__file__).resolve().parent.parent / "models"
REAL_MODEL = next(iter(sorted(MODELS_DIR.glob("*.onnx"))), None)

requires_real_model = pytest.mark.skipif(
    REAL_MODEL is None, reason="no Piper voice model present under models/"
)


def _mp3_available():
    try:
        import lameenc  # noqa: F401
    except ImportError:
        return False
    return True


# --- document_from_text ----------------------------------------------------


def test_document_from_text_splits_paragraphs():
    document = document_from_text("First para.\n\nSecond para.")
    assert [b.text for b in document.blocks] == ["First para.", "Second para."]


def test_document_from_text_empty_raises():
    with pytest.raises(AloudError):
        document_from_text("   \n\n  ")


# --- CLI: stdin and MP3 -----------------------------------------------------


def test_cli_reads_stdin_with_dash(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("Hello from a pipe. Second sentence."))
    exit_code = cli_main(["-", "--dump-text"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Hello from a pipe." in captured.out


def test_cli_empty_stdin_gives_clean_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("   "))
    exit_code = cli_main(["-", "--dump-text"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "error:" in captured.err


@requires_real_model
@pytest.mark.skipif(not _mp3_available(), reason="lameenc not installed")
def test_cli_mp3_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("A short MP3 test."))
    out = tmp_path / "out.mp3"
    exit_code = cli_main(["-", "-o", str(out), "--quiet"])
    assert exit_code == 0
    data = out.read_bytes()
    # MPEG frame sync (0xFFEx) or ID3 tag
    assert data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)


def test_cli_mp3_without_lameenc_gives_clean_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("aloud.__main__.is_mp3_export_available", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO("Hello."))
    exit_code = cli_main(["-", "-o", str(tmp_path / "x.mp3")])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "lameenc" in captured.err


# --- web: pasted text -------------------------------------------------------


@pytest.fixture()
def server():
    from aloud.web import create_server

    server = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _request(server, path, method="GET", body=None):
    port = server.server_address[1]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method
    )
    request.add_header("X-Aloud-Token", server.RequestHandlerClass.token)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def test_web_open_text(server):
    result = _request(
        server,
        "/api/open-text",
        method="POST",
        body="Pasted paragraph one.\n\nPasted paragraph two.".encode(),
    )
    assert result["name"] == "Pasted text"
    flat = [c for b in result["blocks"] for c in b["chunks"]]
    assert "Pasted paragraph one." in flat
    assert "Pasted paragraph two." in flat


# --- web: export cancellation ----------------------------------------------


def test_web_export_cancel(tmp_path, monkeypatch):
    from aloud import web as web_module

    state = web_module.WebState(models_dir=tmp_path)
    state.doc_name = "doc.txt"
    state.doc_full_text = "Hello there."

    fake_engine = types.SimpleNamespace(name="fake")
    monkeypatch.setattr(web_module, "get_engine", lambda name, model_path=None: fake_engine)

    created = []

    def slow_synthesize(text, out_path, engine, speed=1.0, progress=None):
        created.append(out_path)
        pathlib.Path(out_path).write_bytes(b"RIFFpartial")
        for i in range(200):
            progress(i + 1, 200)  # raises _ExportCancelled once flagged
            time.sleep(0.01)
        return {"chunks": 200, "duration_sec": 2.0, "sample_rate": 22050}

    monkeypatch.setattr(web_module, "synthesize_to_wav", slow_synthesize)

    assert state.start_export("wav", "", 1.0) is True
    time.sleep(0.05)
    assert state.cancel_export() is True

    for _ in range(100):
        with state.lock:
            if state.export_job["state"] != "running":
                break
        time.sleep(0.02)
    with state.lock:
        assert state.export_job["state"] == "cancelled"
    # The partial file must not linger.
    assert created and not pathlib.Path(created[0]).exists()
    # A second cancel with nothing running reports False.
    assert state.cancel_export() is False


# --- portable models dir ----------------------------------------------------


def test_models_dir_env_override(monkeypatch, tmp_path):
    from aloud import paths

    monkeypatch.setenv("ALOUD_MODELS_DIR", str(tmp_path))
    assert paths.models_dir() == tmp_path


def test_models_dir_prefers_repo_checkout(monkeypatch):
    from aloud import paths

    monkeypatch.delenv("ALOUD_MODELS_DIR", raising=False)
    assert paths.models_dir() == paths._REPO_MODELS_DIR


def test_models_dir_falls_back_to_xdg_data_home(monkeypatch, tmp_path):
    from aloud import paths

    monkeypatch.delenv("ALOUD_MODELS_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(paths, "_REPO_MODELS_DIR", tmp_path / "does-not-exist")
    assert paths.models_dir() == tmp_path / "aloud" / "models"
