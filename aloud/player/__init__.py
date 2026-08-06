"""Threaded, chunk-streaming audio playback with play/pause/stop.

Backend contract (duck-typed, no ABC needed since only this module and
tests implement it):
    play(pcm_bytes: bytes, sample_rate: int) -> None
        Play one slice of 16-bit mono PCM, blocking until it finishes.
    stop() -> None
        Halt any in-progress playback immediately.
    close() -> None
        Release backend resources.

Live audio playback is non-load-bearing for Aloud's acceptance criteria:
every touch of `sounddevice` is guarded, so a machine with no PortAudio
(like this dev host) degrades to `AudioDeviceUnavailableError` instead of
crashing, and the GUI can fall back to "export to WAV only".
"""

import threading

import numpy as np

from aloud.errors import AudioDeviceUnavailableError
from aloud.tts.chunker import chunk_text
from aloud.tts.writer import iter_chunk_audio

_SLICE_SECONDS = 0.1  # ~100ms slices, so pause/stop stay responsive

_PORTAUDIO_HINT = (
    "Audio playback is unavailable: the PortAudio library is not "
    "installed. Install it via your OS package manager, e.g. "
    "'sudo apt install libportaudio2' or 'sudo dnf install portaudio'."
)


def is_audio_available() -> bool:
    """True/False, never raises."""
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        return False
    try:
        sd.query_devices()
    except Exception:
        return False
    return True


class SoundDeviceBackend:
    """Real audio output via sounddevice/PortAudio."""

    def __init__(self):
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise AudioDeviceUnavailableError(_PORTAUDIO_HINT) from exc
        self._sd = sd

    def play(self, pcm_bytes, sample_rate):
        array = np.frombuffer(pcm_bytes, dtype=np.int16)
        self._sd.play(array, sample_rate)
        self._sd.wait()

    def stop(self):
        self._sd.stop()

    def close(self):
        self._sd.stop()


class NullBackend:
    """Discards audio. Used headlessly and as the no-device GUI fallback.

    Never sleeps for the real audio duration - callers get it back
    immediately, which is what keeps threaded playback tests fast.
    """

    def play(self, pcm_bytes, sample_rate):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class Player:
    """Streams engine.synth() output to a backend in ~100ms slices."""

    def __init__(self, engine, backend=None):
        self._engine = engine
        self._backend = backend
        self._thread = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # set = not paused
        self._state = "idle"
        self._state_lock = threading.Lock()

        self.on_state = None
        self.on_progress = None
        self.on_error = None
        self.last_error = None

    @property
    def state(self):
        with self._state_lock:
            return self._state

    def _set_state(self, new_state):
        with self._state_lock:
            self._state = new_state
        self._safe_call(self.on_state, new_state)

    def _safe_call(self, callback, *args):
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            pass

    def _ensure_backend(self):
        if self._backend is None:
            self._backend = SoundDeviceBackend()
        return self._backend

    def play(self, text, speed=1.0):
        if self._thread is not None and self._thread.is_alive():
            self.stop()

        self.last_error = None
        self._stop_event.clear()
        self._pause_event.set()
        self._thread = threading.Thread(
            target=self._worker, args=(text, speed), daemon=True
        )
        self._thread.start()

    def pause(self):
        self._pause_event.clear()
        if self.state == "playing":
            self._set_state("paused")

    def resume(self):
        self._pause_event.set()
        if self.state == "paused":
            self._set_state("playing")

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()  # unblock a paused worker so it can exit
        if self._backend is not None:
            try:
                self._backend.stop()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._set_state("idle")

    def _worker(self, text, speed):
        self._set_state("playing")
        stopped = False
        try:
            backend = self._ensure_backend()
            sample_rate = self._engine.sample_rate
            slice_frames = max(1, int(sample_rate * _SLICE_SECONDS))
            slice_bytes = slice_frames * 2  # 16-bit mono
            total = len(chunk_text(text))

            for index, pcm in iter_chunk_audio(text, self._engine, speed=speed):
                offset = 0
                while offset < len(pcm):
                    self._pause_event.wait()
                    if self._stop_event.is_set():
                        stopped = True
                        break
                    backend.play(pcm[offset : offset + slice_bytes], sample_rate)
                    offset += slice_bytes
                if stopped or self._stop_event.is_set():
                    stopped = True
                    break
                self._safe_call(self.on_progress, index + 1, total)
        except Exception as exc:
            self.last_error = exc
            self._safe_call(self.on_error, exc)
            self._set_state("idle")
            return

        self._set_state("idle" if stopped else "finished")


__all__ = ["Player", "is_audio_available", "SoundDeviceBackend", "NullBackend"]
