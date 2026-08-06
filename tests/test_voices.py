"""Tests for the voice catalog/downloader (aloud.voices) and its web API.

All network access is monkeypatched - these tests never touch the internet.
"""

import io
import json
import threading
import urllib.error
import urllib.request

import pytest

from aloud import voices
from aloud.errors import AloudError

_SAMPLE_CATALOG = {
    "de_DE-thorsten-high": {
        "key": "de_DE-thorsten-high",
        "language": {"code": "de_DE", "name_english": "German"},
        "quality": "high",
        "num_speakers": 1,
        "files": {
            "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx": {"size_bytes": 100 * 1024 * 1024},
            "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json": {"size_bytes": 4000},
        },
    },
    "en_GB-alba-medium": {
        "key": "en_GB-alba-medium",
        "language": {"code": "en_GB", "name_english": "English"},
        "quality": "medium",
        "num_speakers": 1,
        "files": {
            "en/en_GB/alba/medium/en_GB-alba-medium.onnx": {"size_bytes": 60 * 1024 * 1024},
        },
    },
}


class _FakeResponse:
    def __init__(self, payload):
        self._buffer = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, size=-1):
        return self._buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _fake_urlopen_factory(responses):
    """responses: url-substring -> payload bytes (or an Exception to raise)."""

    def fake_urlopen(url, timeout=None):
        for fragment, payload in responses.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return _FakeResponse(payload)
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    return fake_urlopen


# --- id parsing -------------------------------------------------------------


def test_voice_repo_dir_maps_id_to_repo_path():
    assert voices.voice_repo_dir("en_US-lessac-medium") == "en/en_US/lessac/medium"
    assert voices.voice_repo_dir("de_DE-thorsten-high") == "de/de_DE/thorsten/high"
    # underscores inside the name segment survive
    assert voices.voice_repo_dir("en_US-hfc_female-medium") == "en/en_US/hfc_female/medium"


@pytest.mark.parametrize("bad_id", ["", "nonsense", "en_US-", "-x-low", "en_US"])
def test_voice_repo_dir_rejects_bad_ids(bad_id):
    with pytest.raises(AloudError, match="Invalid voice id"):
        voices.voice_repo_dir(bad_id)


# --- catalog ----------------------------------------------------------------


def test_fetch_catalog_normalizes_and_sorts(monkeypatch):
    payload = json.dumps(_SAMPLE_CATALOG).encode()
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_urlopen_factory({"voices.json": payload})
    )
    catalog = voices.fetch_catalog()
    assert [v["id"] for v in catalog] == ["de_DE-thorsten-high", "en_GB-alba-medium"]
    thorsten = catalog[0]
    assert thorsten["language"] == "German"
    assert thorsten["quality"] == "high"
    assert thorsten["size_mb"] == 100


def test_fetch_catalog_network_error_is_clean(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _fake_urlopen_factory({"voices.json": urllib.error.URLError("no route")}),
    )
    with pytest.raises(AloudError, match="internet connection"):
        voices.fetch_catalog()


# --- download ---------------------------------------------------------------


def test_download_voice_writes_both_files_with_progress(monkeypatch, tmp_path):
    onnx_payload = b"O" * 2048
    json_payload = b'{"sample_rate": 22050}'
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _fake_urlopen_factory(
            {
                "en_GB-alba-medium.onnx.json": json_payload,
                "en_GB-alba-medium.onnx": onnx_payload,
            }
        ),
    )
    seen = []
    paths = voices.download_voice(
        "en_GB-alba-medium",
        models_dir=tmp_path,
        progress=lambda name, received, total: seen.append((name, received, total)),
    )
    assert (tmp_path / "en_GB-alba-medium.onnx").read_bytes() == onnx_payload
    assert (tmp_path / "en_GB-alba-medium.onnx.json").read_bytes() == json_payload
    assert len(paths) == 2
    assert not list(tmp_path.glob("*.part"))
    assert any(name == "en_GB-alba-medium.onnx" for name, _, _ in seen)
    assert voices.installed_voices(tmp_path) == {"en_GB-alba-medium"}


def test_download_unknown_voice_gives_clean_error(monkeypatch, tmp_path):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory({}))
    with pytest.raises(AloudError, match="was not found"):
        voices.download_voice("xx_XX-nobody-low", models_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- CLI --------------------------------------------------------------------


def test_cli_list_filters_and_marks_installed(monkeypatch, tmp_path, capsys):
    payload = json.dumps(_SAMPLE_CATALOG).encode()
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_urlopen_factory({"voices.json": payload})
    )
    (tmp_path / "en_GB-alba-medium.onnx").write_bytes(b"x")

    exit_code = voices.main(["--models-dir", str(tmp_path), "list", "english"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "* en_GB-alba-medium" in out
    assert "de_DE-thorsten-high" not in out


def test_cli_download_invalid_id_exits_2(monkeypatch, tmp_path, capsys):
    exit_code = voices.main(["--models-dir", str(tmp_path), "download", "nonsense"])
    assert exit_code == 2
    assert "Invalid voice id" in capsys.readouterr().err


# --- web API ----------------------------------------------------------------


@pytest.fixture()
def web_server(tmp_path):
    from aloud.web import create_server

    server = create_server(port=0, models_dir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _web_json(server, path, method="GET", body=None, headers=None):
    port = server.server_address[1]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method
    )
    request.add_header("X-Aloud-Token", server.RequestHandlerClass.token)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def test_web_catalog_marks_installed(web_server, monkeypatch, tmp_path):
    monkeypatch.setattr(voices, "fetch_catalog", lambda timeout=30: [
        {"id": "en_GB-alba-medium", "language": "English", "language_code": "en_GB",
         "quality": "medium", "size_mb": 60, "num_speakers": 1},
    ])
    (tmp_path / "en_GB-alba-medium.onnx").write_bytes(b"x")

    result = _web_json(web_server, "/api/voices/catalog")
    assert result["voices"][0]["id"] == "en_GB-alba-medium"
    assert result["voices"][0]["installed"] is True


def test_web_voice_download_flow(web_server, monkeypatch, tmp_path):
    def fake_download(voice_id, models_dir=None, progress=None, timeout=60):
        if progress:
            progress(f"{voice_id}.onnx", 512, 1024)
            progress(f"{voice_id}.onnx", 1024, 1024)
        (tmp_path / f"{voice_id}.onnx").write_bytes(b"model")
        (tmp_path / f"{voice_id}.onnx.json").write_bytes(b"{}")
        return [str(tmp_path / f"{voice_id}.onnx")]

    monkeypatch.setattr(voices, "download_voice", fake_download)

    started = _web_json(
        web_server,
        "/api/voices/download",
        method="POST",
        body=json.dumps({"id": "en_GB-alba-medium"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert started == {"started": True}

    status = {"state": "running"}
    for _ in range(100):
        status = _web_json(web_server, "/api/voices/download/status")
        if status["state"] != "running":
            break
        threading.Event().wait(0.05)
    assert status["state"] == "done", status
    assert (tmp_path / "en_GB-alba-medium.onnx").exists()

    # The newly installed voice must now appear in /api/state.
    state = _web_json(web_server, "/api/state")
    assert "en_GB-alba-medium.onnx" in state["voices"]


def test_web_voice_download_rejects_bad_id(web_server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _web_json(
            web_server,
            "/api/voices/download",
            method="POST",
            body=json.dumps({"id": "nonsense"}).encode(),
            headers={"Content-Type": "application/json"},
        )
    assert excinfo.value.code == 400
