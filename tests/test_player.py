import threading
import time

import pytest

from aloud.errors import AudioDeviceUnavailableError
from aloud.player import NullBackend, Player, SoundDeviceBackend, is_audio_available
from aloud.tts.writer import iter_chunk_audio


def _wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _FakeEngine:
    """Deterministic fake: each call returns a distinctly-tagged PCM run,
    long enough (0.5s at sample_rate=1000) to sub-divide into several
    ~100ms slices, so slicing/order/completeness are all exercisable."""

    sample_rate = 1000

    def __init__(self):
        self.call_count = 0

    def synth(self, text, speed=1.0):
        self.call_count += 1
        marker = self.call_count % 256
        n_frames = 500
        return bytes([marker, marker]) * n_frames

    def close(self):
        pass


class _RaisingEngine:
    sample_rate = 1000

    def synth(self, text, speed=1.0):
        raise RuntimeError("synthesis boom")

    def close(self):
        pass


class _RecordingBackend:
    def __init__(self):
        self.slices = []
        self._lock = threading.Lock()

    def play(self, pcm_bytes, sample_rate):
        with self._lock:
            self.slices.append(pcm_bytes)

    def stop(self):
        pass

    def close(self):
        pass


class _GatedRecordingBackend:
    """Like _RecordingBackend, but blocks inside play() after the Nth
    slice until the test calls release() - lets tests pause/stop the
    player at a precise, deterministic point in the stream."""

    def __init__(self, release_after):
        self.slices = []
        self._lock = threading.Lock()
        self._gate = threading.Event()
        self._gate.set()
        self._release_after = release_after
        self.hit_gate = threading.Event()

    def play(self, pcm_bytes, sample_rate):
        with self._lock:
            self.slices.append(pcm_bytes)
            count = len(self.slices)
        if count == self._release_after:
            self._gate.clear()
            self.hit_gate.set()
            self._gate.wait(timeout=5.0)

    def release(self):
        self._gate.set()

    def stop(self):
        pass

    def close(self):
        pass


TEXT = "Sentence one is here. Sentence two follows along. Sentence three ends it."


# --- streaming fidelity ------------------------------------------------------


def test_slices_are_complete_ordered_and_not_duplicated():
    backend = _RecordingBackend()
    player = Player(_FakeEngine(), backend=backend)

    player.play(TEXT)
    assert _wait_until(lambda: player.state == "finished")

    expected = b"".join(pcm for _, pcm in iter_chunk_audio(TEXT, _FakeEngine()))
    actual = b"".join(backend.slices)
    assert actual == expected


def test_slices_are_approximately_100ms_or_smaller():
    backend = _RecordingBackend()
    player = Player(_FakeEngine(), backend=backend)

    player.play(TEXT)
    assert _wait_until(lambda: player.state == "finished")

    # sample_rate=1000, 100ms = 100 frames = 200 bytes (16-bit mono)
    assert backend.slices  # sanity: something was actually recorded
    assert all(len(s) <= 200 for s in backend.slices)


def test_play_returns_immediately():
    backend = _RecordingBackend()
    player = Player(_FakeEngine(), backend=backend)

    start = time.monotonic()
    player.play(TEXT)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert _wait_until(lambda: player.state == "finished")


# --- stop / pause / resume ---------------------------------------------------


def test_stop_mid_stream_halts_early():
    backend = _GatedRecordingBackend(release_after=2)
    player = Player(_FakeEngine(), backend=backend)

    player.play(TEXT)
    assert backend.hit_gate.wait(timeout=5.0)

    player.stop()
    backend.release()

    assert _wait_until(lambda: player.state == "idle")

    expected_full = b"".join(pcm for _, pcm in iter_chunk_audio(TEXT, _FakeEngine()))
    actual = b"".join(backend.slices)
    assert len(actual) < len(expected_full)


def test_pause_then_resume_no_audio_while_paused():
    backend = _GatedRecordingBackend(release_after=2)
    player = Player(_FakeEngine(), backend=backend)

    player.play(TEXT)
    assert backend.hit_gate.wait(timeout=5.0)

    player.pause()
    backend.release()
    assert _wait_until(lambda: player.state == "paused")

    count_after_pause = len(backend.slices)
    time.sleep(0.1)
    assert len(backend.slices) == count_after_pause

    player.resume()
    assert _wait_until(lambda: player.state == "finished")

    expected_full = b"".join(pcm for _, pcm in iter_chunk_audio(TEXT, _FakeEngine()))
    actual = b"".join(backend.slices)
    assert actual == expected_full


def test_stop_when_idle_is_noop_and_double_stop_is_safe():
    player = Player(_FakeEngine(), backend=_RecordingBackend())
    assert player.state == "idle"

    player.stop()
    assert player.state == "idle"

    player.stop()
    assert player.state == "idle"


# --- error handling -----------------------------------------------------------


def test_synthesis_error_is_captured_not_raised():
    errors = []
    player = Player(_RaisingEngine(), backend=_RecordingBackend())
    player.on_error = lambda exc: errors.append(exc)

    player.play(TEXT)
    assert _wait_until(lambda: player.state == "idle")

    assert isinstance(player.last_error, RuntimeError)
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


def test_callback_exception_does_not_kill_worker():
    backend = _RecordingBackend()
    player = Player(_FakeEngine(), backend=backend)
    player.on_progress = lambda index, total: (_ for _ in ()).throw(ValueError("boom"))

    player.play(TEXT)
    assert _wait_until(lambda: player.state == "finished")
    assert backend.slices  # stream still ran to completion


# --- audio-device availability (this host has no PortAudio) -----------------


def test_is_audio_available_returns_bool_without_raising():
    result = is_audio_available()
    assert isinstance(result, bool)


@pytest.mark.skipif(
    is_audio_available(), reason="PortAudio is available on this machine"
)
def test_sounddevice_backend_raises_audio_device_unavailable():
    with pytest.raises(AudioDeviceUnavailableError) as exc_info:
        SoundDeviceBackend()
    assert "\n" not in exc_info.value.user_message


def test_player_construction_never_raises_without_audio_device():
    # backend=None means "resolve lazily on first play()" - constructing
    # must be cheap and safe even with no audio device present.
    player = Player(_FakeEngine())
    assert player.state == "idle"
