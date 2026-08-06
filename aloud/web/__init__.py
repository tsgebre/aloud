"""Browser-based GUI for Aloud. Stdlib only: http.server + one HTML page.

`python -m aloud.web` starts a server bound to 127.0.0.1 and opens the
default browser. The browser plays the audio itself, so live playback works
even where Tkinter (needs X11) or PortAudio are unavailable.

Security model: the server only listens on loopback, and every /api request
must carry the X-Aloud-Token header. The token is generated per run and
embedded in the served page, so scripts on other origins (which can reach
localhost but cannot read our responses) cannot drive the API.

Thread-safety: ThreadingHTTPServer handles each request on its own thread.
`state.lock` guards document/cache mutation; engines are wrapped in
SynchronizedEngine (per-call lock), so concurrent playback and export
interleave chunk-by-chunk instead of corrupting the engine or starving
each other. `state.engine_lock` only guards engine-cache creation.
"""

import io
import json
import os
import pathlib
import re
import secrets
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from aloud import voices as voice_catalog
from aloud.errors import AloudError
from aloud.extract import document_from_text, extract_text
from aloud.paths import models_dir as default_models_dir
from aloud.tts import get_engine
from aloud.tts.base import SynchronizedEngine
from aloud.tts.chunker import chunk_text
from aloud.tts.writer import is_mp3_export_available, synthesize_to_mp3, synthesize_to_wav

PAGE_PATH = pathlib.Path(__file__).resolve().parent / "page.html"

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
AUDIO_CACHE_SIZE = 8

_AUDIO_ROUTE_RE = re.compile(r"^/api/audio/(\d+)$")


class _ExportCancelled(Exception):
    """Raised inside the export worker when the client asked to cancel."""


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw 16-bit mono PCM in an in-memory WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


class WebState:
    """All mutable server-side state for one running Aloud web session."""

    def __init__(self, models_dir=None):
        self.lock = threading.Lock()
        self.engine_lock = threading.Lock()
        self.models_dir = pathlib.Path(models_dir) if models_dir else default_models_dir()

        self.doc_name = None
        self.doc_full_text = None
        self.doc_blocks = []  # [{"kind": ..., "chunks": [...]}]
        self.chunks = []  # flat, in reading order

        self._engines = {}  # voice_path (str|None) -> Engine
        self._audio_cache = {}  # (voice_path, index) -> wav bytes
        self._audio_cache_order = []  # LRU order, oldest first

        self.export_job = None  # {"state", "index", "total", "path", "fmt", "error", "result"}
        self.voice_job = None  # {"state", "id", "file", "received", "total", "error"}
        self._catalog = None  # cached fetch_catalog() result

    # --- voices -------------------------------------------------------

    def voice_names(self):
        if not self.models_dir.is_dir():
            return []
        return sorted(f.name for f in self.models_dir.glob("*.onnx"))

    def resolve_voice(self, name):
        """Map a client-supplied voice name to a model path, or None for auto.

        Only bare names actually present in the models directory are accepted;
        anything else (including path separators) falls back to auto, so the
        client can never point the engine at an arbitrary filesystem path.
        """
        if name and name in self.voice_names():
            return str(self.models_dir / name)
        return None

    def get_engine_for(self, voice_path):
        with self.engine_lock:
            if voice_path not in self._engines:
                self._engines[voice_path] = SynchronizedEngine(
                    get_engine("auto", model_path=voice_path)
                )
            return self._engines[voice_path]

    def get_catalog(self):
        """Fetched once per server run; voices rarely change mid-session."""
        with self.lock:
            if self._catalog is not None:
                return self._catalog
        catalog = voice_catalog.fetch_catalog()
        with self.lock:
            self._catalog = catalog
        return catalog

    def start_voice_download(self, voice_id):
        """Kick off a background voice download. False if one is running."""
        voice_catalog.voice_repo_dir(voice_id)  # validate id early -> AloudError
        with self.lock:
            if self.voice_job is not None and self.voice_job["state"] == "running":
                return False
            job = {
                "state": "running",
                "id": voice_id,
                "file": "",
                "received": 0,
                "total": None,
                "error": None,
            }
            self.voice_job = job
        thread = threading.Thread(
            target=self._voice_download_worker, args=(job, voice_id), daemon=True
        )
        thread.start()
        return True

    def _voice_download_worker(self, job, voice_id):
        def progress(file_name, received, total):
            with self.lock:
                job["file"] = file_name
                job["received"] = received
                job["total"] = total

        try:
            voice_catalog.download_voice(
                voice_id, models_dir=self.models_dir, progress=progress
            )
        except AloudError as exc:
            with self.lock:
                job["state"] = "error"
                job["error"] = exc.user_message
            return
        except Exception as exc:
            with self.lock:
                job["state"] = "error"
                job["error"] = f"unexpected failure: {type(exc).__name__}: {exc}"
            return
        with self.lock:
            job["state"] = "done"

    # --- document -----------------------------------------------------

    def load_document(self, name, payload):
        suffix = os.path.splitext(name)[1].lower()
        # extract_text dispatches on suffix, so give the temp file the same one.
        fd, tmp_path = tempfile.mkstemp(suffix=suffix or ".bin")
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                tmp_file.write(payload)
            document = extract_text(tmp_path)
        finally:
            os.unlink(tmp_path)
        return self._set_document(name, document)

    def load_text(self, name, text):
        return self._set_document(name, document_from_text(text, source=name))

    def _set_document(self, name, document):
        blocks = [
            {"kind": block.kind, "chunks": chunk_text(block.text)}
            for block in document.blocks
        ]
        with self.lock:
            self.doc_name = os.path.basename(name)
            self.doc_full_text = document.full_text
            self.doc_blocks = blocks
            self.chunks = [c for b in blocks for c in b["chunks"]]
            self._audio_cache.clear()
            self._audio_cache_order.clear()
        return {
            "name": self.doc_name,
            "title": document.title,
            "blocks": blocks,
            "chunks_total": len(self.chunks),
        }

    # --- chunk audio --------------------------------------------------

    def chunk_wav(self, index, voice_name):
        with self.lock:
            if not 0 <= index < len(self.chunks):
                raise IndexError(index)
            chunk = self.chunks[index]
        voice_path = self.resolve_voice(voice_name)
        key = (voice_path, index)

        with self.lock:
            if key in self._audio_cache:
                return self._audio_cache[key]

        engine = self.get_engine_for(voice_path)
        # Playback speed is applied client-side via audio.playbackRate, so
        # chunks are always synthesized at 1.0 and stay cacheable across
        # speed changes.
        pcm = engine.synth(chunk, speed=1.0)
        wav = pcm_to_wav_bytes(pcm, engine.sample_rate)

        with self.lock:
            self._audio_cache[key] = wav
            self._audio_cache_order.append(key)
            while len(self._audio_cache_order) > AUDIO_CACHE_SIZE:
                evicted = self._audio_cache_order.pop(0)
                self._audio_cache.pop(evicted, None)
        return wav

    # --- export -------------------------------------------------------

    def start_export(self, fmt, voice_name, speed):
        with self.lock:
            if self.doc_full_text is None:
                raise AloudError("Open a document first.")
            if self.export_job is not None and self.export_job["state"] == "running":
                return False
            # A finished export the client never downloaded would otherwise
            # leak its temp file when the job slot is reused.
            stale_path = (self.export_job or {}).get("path")
            text = self.doc_full_text
            job = {
                "state": "running",
                "index": 0,
                "total": len(chunk_text(text)),
                "path": None,
                "fmt": fmt,
                "error": None,
                "result": None,
                "cancel": False,
            }
            self.export_job = job

        if stale_path and os.path.exists(stale_path):
            os.unlink(stale_path)
        voice_path = self.resolve_voice(voice_name)
        thread = threading.Thread(
            target=self._export_worker, args=(job, text, fmt, voice_path, speed), daemon=True
        )
        thread.start()
        return True

    def cancel_export(self):
        """Flag the running export to stop at the next chunk boundary."""
        with self.lock:
            if self.export_job is None or self.export_job["state"] != "running":
                return False
            self.export_job["cancel"] = True
            return True

    def _export_worker(self, job, text, fmt, voice_path, speed):
        def progress(index, total):
            with self.lock:
                job["index"] = index
                job["total"] = total
                cancelled = job["cancel"]
            if cancelled:
                raise _ExportCancelled()

        fd, tmp_path = tempfile.mkstemp(suffix=f".{fmt}")
        os.close(fd)
        try:
            engine = self.get_engine_for(voice_path)
            if fmt == "mp3":
                result = synthesize_to_mp3(text, tmp_path, engine, speed=speed, progress=progress)
            else:
                result = synthesize_to_wav(text, tmp_path, engine, speed=speed, progress=progress)
        except _ExportCancelled:
            os.unlink(tmp_path)
            with self.lock:
                job["state"] = "cancelled"
            return
        except AloudError as exc:
            os.unlink(tmp_path)
            with self.lock:
                job["state"] = "error"
                job["error"] = exc.user_message
            return
        except Exception as exc:
            os.unlink(tmp_path)
            with self.lock:
                job["state"] = "error"
                job["error"] = f"unexpected failure: {type(exc).__name__}: {exc}"
            return
        with self.lock:
            job["path"] = tmp_path
            job["result"] = result
            job["state"] = "done"

    def take_export_file(self):
        """Return (path, fmt, download_name) for a finished export and clear the job."""
        with self.lock:
            job = self.export_job
            if job is None or job["state"] != "done":
                return None
            self.export_job = None
        stem = os.path.splitext(self.doc_name or "narration")[0]
        return job["path"], job["fmt"], f"{stem}.{job['fmt']}"


class AloudRequestHandler(BaseHTTPRequestHandler):
    # Subclasses created by create_server() set these.
    state: WebState = None
    token: str = None
    page_html: str = None

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # keep the terminal quiet; errors surface in the UI instead

    # --- response helpers ---------------------------------------------

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code, content_type, body, extra_headers=()):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, exc):
        if isinstance(exc, AloudError):
            self._send_json(400, {"error": exc.user_message})
        else:
            self._send_json(500, {"error": f"unexpected failure: {type(exc).__name__}: {exc}"})

    def _authorized(self):
        if self.headers.get("X-Aloud-Token") == self.token:
            return True
        self._send_json(403, {"error": "missing or invalid token"})
        return False

    # --- routing ------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                body = self.page_html.encode("utf-8")
                self._send_bytes(200, "text/html; charset=utf-8", body)
                return
            if not parsed.path.startswith("/api/"):
                self._send_json(404, {"error": "not found"})
                return
            if not self._authorized():
                return
            if parsed.path == "/api/state":
                self._handle_state()
            elif _AUDIO_ROUTE_RE.match(parsed.path):
                index = int(_AUDIO_ROUTE_RE.match(parsed.path).group(1))
                self._handle_audio(index, parsed)
            elif parsed.path == "/api/export/status":
                self._handle_export_status()
            elif parsed.path == "/api/export/file":
                self._handle_export_file()
            elif parsed.path == "/api/voices/catalog":
                self._handle_voice_catalog()
            elif parsed.path == "/api/voices/download/status":
                self._handle_voice_download_status()
            else:
                self._send_json(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self._send_error_json(exc)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if not parsed.path.startswith("/api/"):
                self._send_json(404, {"error": "not found"})
                return
            if not self._authorized():
                return
            if parsed.path == "/api/open":
                self._handle_open(parsed)
            elif parsed.path == "/api/open-text":
                self._handle_open_text(parsed)
            elif parsed.path == "/api/export":
                self._handle_export_start()
            elif parsed.path == "/api/export/cancel":
                self._handle_export_cancel()
            elif parsed.path == "/api/voices/download":
                self._handle_voice_download_start()
            else:
                self._send_json(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self._send_error_json(exc)

    # --- handlers -----------------------------------------------------

    def _handle_state(self):
        with self.state.lock:
            doc = None
            if self.state.doc_name is not None:
                doc = {
                    "name": self.state.doc_name,
                    "blocks": self.state.doc_blocks,
                    "chunks_total": len(self.state.chunks),
                }
        self._send_json(
            200,
            {
                "voices": self.state.voice_names(),
                "mp3_available": is_mp3_export_available(),
                "doc": doc,
            },
        )

    def _handle_open(self, parsed):
        query = parse_qs(parsed.query)
        name = (query.get("name") or [""])[0]
        if not name:
            self._send_json(400, {"error": "missing ?name= file name"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._send_json(400, {"error": "empty upload"})
            return
        if length > MAX_UPLOAD_BYTES:
            self._send_json(413, {"error": "file too large"})
            return
        payload = self.rfile.read(length)
        try:
            result = self.state.load_document(name, payload)
        except (AloudError, Exception) as exc:
            self._send_error_json(exc)
            return
        self._send_json(200, result)

    def _handle_open_text(self, parsed):
        query = parse_qs(parsed.query)
        name = (query.get("name") or ["Pasted text"])[0] or "Pasted text"
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            self._send_json(413, {"error": "text too large"})
            return
        text = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            result = self.state.load_text(name, text)
        except (AloudError, Exception) as exc:
            self._send_error_json(exc)
            return
        self._send_json(200, result)

    def _handle_export_cancel(self):
        self._send_json(200, {"cancelled": self.state.cancel_export()})

    def _handle_audio(self, index, parsed):
        query = parse_qs(parsed.query)
        voice = (query.get("voice") or [""])[0]
        try:
            wav = self.state.chunk_wav(index, voice)
        except IndexError:
            self._send_json(404, {"error": f"no chunk {index}"})
            return
        except (AloudError, Exception) as exc:
            self._send_error_json(exc)
            return
        self._send_bytes(200, "audio/wav", wav, [("Cache-Control", "no-store")])

    def _handle_export_start(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        fmt = body.get("fmt", "wav")
        if fmt not in ("wav", "mp3"):
            self._send_json(400, {"error": f"unsupported export format '{fmt}'"})
            return
        if fmt == "mp3" and not is_mp3_export_available():
            self._send_json(400, {"error": "MP3 export requires the optional 'lameenc' package"})
            return
        try:
            speed = float(body.get("speed", 1.0))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid speed"})
            return
        try:
            started = self.state.start_export(fmt, body.get("voice", ""), speed)
        except AloudError as exc:
            self._send_error_json(exc)
            return
        if not started:
            self._send_json(409, {"error": "an export is already running"})
            return
        self._send_json(200, {"started": True})

    def _handle_export_status(self):
        with self.state.lock:
            job = self.state.export_job
            if job is None:
                self._send_json(200, {"state": "idle"})
                return
            self._send_json(
                200,
                {
                    "state": job["state"],
                    "index": job["index"],
                    "total": job["total"],
                    "error": job["error"],
                    "result": job["result"],
                },
            )

    def _handle_voice_catalog(self):
        try:
            catalog = self.state.get_catalog()
        except (AloudError, Exception) as exc:
            self._send_error_json(exc)
            return
        installed = voice_catalog.installed_voices(self.state.models_dir)
        self._send_json(
            200,
            {
                "voices": [
                    dict(voice, installed=voice["id"] in installed) for voice in catalog
                ]
            },
        )

    def _handle_voice_download_start(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        voice_id = body.get("id", "")
        try:
            started = self.state.start_voice_download(voice_id)
        except AloudError as exc:
            self._send_error_json(exc)
            return
        if not started:
            self._send_json(409, {"error": "a voice download is already running"})
            return
        self._send_json(200, {"started": True})

    def _handle_voice_download_status(self):
        with self.state.lock:
            job = self.state.voice_job
            if job is None:
                self._send_json(200, {"state": "idle"})
                return
            self._send_json(200, dict(job))

    def _handle_export_file(self):
        taken = self.state.take_export_file()
        if taken is None:
            self._send_json(404, {"error": "no finished export"})
            return
        path, fmt, download_name = taken
        try:
            with open(path, "rb") as export_file:
                body = export_file.read()
        finally:
            os.unlink(path)
        content_type = "audio/mpeg" if fmt == "mp3" else "audio/wav"
        self._send_bytes(
            200,
            content_type,
            body,
            [("Content-Disposition", f'attachment; filename="{download_name}"')],
        )


def create_server(port=0, models_dir=None):
    """Build a ready-to-serve ThreadingHTTPServer bound to 127.0.0.1.

    Returns the server; the per-run auth token is available as
    `server.RequestHandlerClass.token` and is already embedded in the page.
    """
    token = secrets.token_urlsafe(16)
    page_html = PAGE_PATH.read_text(encoding="utf-8").replace("__ALOUD_TOKEN__", token)
    handler = type(
        "BoundAloudRequestHandler",
        (AloudRequestHandler,),
        {"state": WebState(models_dir=models_dir), "token": token, "page_html": page_html},
    )
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
