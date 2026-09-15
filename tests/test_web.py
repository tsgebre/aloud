"""Tests for the browser-based GUI server (aloud.web).

These run against a real ThreadingHTTPServer on an ephemeral loopback port —
no browser involved, plain urllib on the client side.
"""

import json
import pathlib
import threading
import urllib.error
import urllib.request

import pytest

from aloud.web import create_server

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"
MODELS_DIR = pathlib.Path(__file__).resolve().parent.parent / "models"
REAL_MODEL = next(iter(sorted(MODELS_DIR.glob("*.onnx"))), None)

requires_real_model = pytest.mark.skipif(
    REAL_MODEL is None, reason="no Piper voice model present under models/"
)


@pytest.fixture()
def server():
    server = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _request(server, path, method="GET", body=None, headers=None, with_token=True):
    port = server.server_address[1]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method
    )
    if with_token:
        request.add_header("X-Aloud-Token", server.RequestHandlerClass.token)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    return urllib.request.urlopen(request, timeout=30)


def _request_json(server, path, **kwargs):
    with _request(server, path, **kwargs) as response:
        return json.loads(response.read())


def _open_sample_txt(server):
    payload = (SAMPLES / "sample.txt").read_bytes()
    return _request_json(server, "/api/open?name=sample.txt", method="POST", body=payload)


# --- page + auth ------------------------------------------------------------


def test_index_serves_page_with_embedded_token(server):
    with _request(server, "/", with_token=False) as response:
        html = response.read().decode("utf-8")
    assert "<title>Aloud</title>" in html
    assert server.RequestHandlerClass.token in html
    assert "__ALOUD_TOKEN__" not in html


def test_api_rejects_missing_token(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(server, "/api/state", with_token=False)
    assert excinfo.value.code == 403


def test_api_rejects_wrong_token(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(
            server, "/api/state", with_token=False, headers={"X-Aloud-Token": "wrong"}
        )
    assert excinfo.value.code == 403


# --- state + open -----------------------------------------------------------


def test_state_lists_voices_and_no_doc(server):
    state = _request_json(server, "/api/state")
    assert state["doc"] is None
    assert isinstance(state["voices"], list)
    assert isinstance(state["mp3_available"], bool)


def test_open_txt_returns_blocks_and_chunks(server):
    result = _open_sample_txt(server)
    assert result["name"] == "sample.txt"
    assert result["chunks_total"] > 0
    flat = [c for b in result["blocks"] for c in b["chunks"]]
    assert len(flat) == result["chunks_total"]
    assert any("The quick brown fox jumps over the lazy dog." in c for c in flat)
    # A later /api/state must report the same loaded document.
    state = _request_json(server, "/api/state")
    assert state["doc"]["name"] == "sample.txt"
    assert state["doc"]["chunks_total"] == result["chunks_total"]


def test_open_multi_resolves_latex_inputs(server):
    import base64

    main_tex = (
        b"\\documentclass{article}\\begin{document}"
        b"\\input{intro}\\input{sections/methods}\\end{document}"
    )
    intro_tex = b"\\section{Introduction}\nOpening words."
    methods_tex = b"\\section{Methods}\nMethod details."
    body = json.dumps(
        {
            "files": [
                {"name": "intro.tex", "data": base64.b64encode(intro_tex).decode()},
                {"name": "main.tex", "data": base64.b64encode(main_tex).decode()},
                # Flattened upload of a subdirectory file: matched by basename.
                {"name": "methods.tex", "data": base64.b64encode(methods_tex).decode()},
            ]
        }
    ).encode()
    result = _request_json(server, "/api/open-multi", method="POST", body=body)
    assert result["name"] == "main.tex"
    flat = [c for b in result["blocks"] for c in b["chunks"]]
    assert any("Opening words." in c for c in flat)
    assert any("Method details." in c for c in flat)
    headings = [b for b in result["blocks"] if b["kind"] == "heading"]
    assert len(headings) == 2


def test_open_url_fetches_and_extracts_html(server):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    page = (
        b"<html><head><title>A Web Article</title></head>"
        b"<body><h1>A Web Article</h1><p>Words fetched over HTTP.</p>"
        b"<script>never.read();</script></body></html>"
    )

    class _Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            pass

    origin = HTTPServer(("127.0.0.1", 0), _Page)
    thread = threading.Thread(target=origin.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{origin.server_address[1]}/article"
        body = json.dumps({"url": url}).encode()
        result = _request_json(server, "/api/open-url", method="POST", body=body)
    finally:
        origin.shutdown()
        origin.server_close()

    flat = [c for b in result["blocks"] for c in b["chunks"]]
    assert any("Words fetched over HTTP." in c for c in flat)
    assert not any("never.read();" in c for c in flat)


def test_open_url_rejects_non_http_scheme(server):
    body = json.dumps({"url": "ftp://example.com/file.txt"}).encode()
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(server, "/api/open-url", method="POST", body=body)
    assert excinfo.value.code == 400
    assert "http" in json.loads(excinfo.value.read())["error"].lower()


def test_open_url_unreachable_gives_clean_error(server):
    # Port 9 (discard) on loopback: nothing listens there.
    body = json.dumps({"url": "http://127.0.0.1:9/"}).encode()
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(server, "/api/open-url", method="POST", body=body)
    assert excinfo.value.code == 400
    assert "Could not fetch URL" in json.loads(excinfo.value.read())["error"]


def test_open_multi_rejects_malformed_body(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(server, "/api/open-multi", method="POST", body=b"not json")
    assert excinfo.value.code == 400


def test_open_unsupported_extension_gives_clean_400(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(server, "/api/open?name=notes.xyz", method="POST", body=b"hello")
    assert excinfo.value.code == 400
    error = json.loads(excinfo.value.read())["error"]
    assert "Unsupported file format" in error


def test_open_without_name_gives_400(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(server, "/api/open", method="POST", body=b"hello")
    assert excinfo.value.code == 400


# --- chunk audio ------------------------------------------------------------


@requires_real_model
def test_chunk_audio_returns_wav(server):
    _open_sample_txt(server)
    with _request(server, "/api/audio/0") as response:
        assert response.headers["Content-Type"] == "audio/wav"
        data = response.read()
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"


def test_chunk_audio_out_of_range_gives_404(server):
    _open_sample_txt(server)
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(server, "/api/audio/99999")
    assert excinfo.value.code == 404


# --- export -----------------------------------------------------------------


@requires_real_model
def test_export_wav_roundtrip(server, tmp_path):
    # Small one-sentence doc so the export finishes quickly.
    doc = tmp_path / "tiny.txt"
    doc.write_text("Hello from the Aloud web export test.")
    _request_json(
        server, "/api/open?name=tiny.txt", method="POST", body=doc.read_bytes()
    )

    body = json.dumps({"fmt": "wav", "voice": "", "speed": 1.0}).encode()
    started = _request_json(
        server,
        "/api/export",
        method="POST",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    assert started == {"started": True}

    status = {"state": "running"}
    for _ in range(600):  # up to ~60s
        status = _request_json(server, "/api/export/status")
        if status["state"] != "running":
            break
        threading.Event().wait(0.1)
    assert status["state"] == "done", status
    assert status["result"]["duration_sec"] > 0

    with _request(server, "/api/export/file") as response:
        assert response.headers["Content-Type"] == "audio/wav"
        assert 'filename="tiny.wav"' in response.headers["Content-Disposition"]
        data = response.read()
    assert data[:4] == b"RIFF"

    # The job is consumed: a second download must 404.
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(server, "/api/export/file")
    assert excinfo.value.code == 404


def test_export_without_document_gives_400(server):
    body = json.dumps({"fmt": "wav"}).encode()
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(
            server,
            "/api/export",
            method="POST",
            body=body,
            headers={"Content-Type": "application/json"},
        )
    assert excinfo.value.code == 400
    assert "Open a document first" in json.loads(excinfo.value.read())["error"]


def test_export_bad_format_gives_400(server):
    _open_sample_txt(server)
    body = json.dumps({"fmt": "ogg"}).encode()
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(
            server,
            "/api/export",
            method="POST",
            body=body,
            headers={"Content-Type": "application/json"},
        )
    assert excinfo.value.code == 400


# --- LAN mode (--host) ------------------------------------------------------


@pytest.fixture()
def lan_server():
    server = create_server(port=0, host="0.0.0.0")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_lan_mode_page_requires_url_token(lan_server):
    port = lan_server.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=30)
    assert excinfo.value.code == 403
    body = excinfo.value.read().decode("utf-8")
    assert lan_server.RequestHandlerClass.token not in body


def test_lan_mode_page_rejects_wrong_url_token(lan_server):
    port = lan_server.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/?t=wrong", timeout=30)
    assert excinfo.value.code == 403


def test_lan_mode_page_served_with_url_token(lan_server):
    port = lan_server.server_address[1]
    token = lan_server.RequestHandlerClass.token
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/?t={token}", timeout=30) as response:
        html = response.read().decode("utf-8")
    assert "<title>Aloud</title>" in html
    assert token in html


def test_lan_mode_api_still_requires_header_token(lan_server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(lan_server, "/api/state", with_token=False)
    assert excinfo.value.code == 403
