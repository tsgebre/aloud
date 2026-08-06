import pathlib
import tkinter as tk

import pytest

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"


def _make_root_or_skip():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for GUI construction test")
    return root


def test_import_aloud_gui_succeeds():
    # Must work with no display: tkinter itself is import-safe headless,
    # only Tk() construction requires an actual display.
    import aloud.gui  # noqa: F401


def test_construct_app_creates_expected_widgets():
    from aloud.gui import AloudApp

    root = _make_root_or_skip()
    try:
        app = AloudApp(root)
        assert app.open_button is not None
        assert app.play_button is not None
        assert app.pause_button is not None
        assert app.stop_button is not None
        assert app.export_button is not None
        assert app.text_widget is not None
        assert app.speed_scale is not None
        assert app.voice_combobox is not None
    finally:
        root.destroy()


def test_load_file_populates_text_widget():
    from aloud.gui import AloudApp

    root = _make_root_or_skip()
    try:
        app = AloudApp(root)
        app.load_file(str(SAMPLES / "sample.txt"))
        content = app.text_widget.get("1.0", "end")
        assert "The quick brown fox jumps over the lazy dog." in content
        assert app._document is not None
    finally:
        root.destroy()


# --- callback thread-safety: no display required ----------------------------


class _FakeRoot:
    """Records after() calls; has no widgets, so any code path that tries
    to touch a widget directly (instead of scheduling via after) would
    raise AttributeError here rather than silently working."""

    def __init__(self):
        self.after_calls = []

    def after(self, delay, func, *args):
        self.after_calls.append((delay, func, args))
        return "fake-after-id"


def test_player_callbacks_marshal_through_after_not_widgets():
    from aloud.gui import AloudApp

    # Bypass __init__ entirely so no real widgets exist - if any callback
    # touched a widget directly it would raise AttributeError immediately.
    app = AloudApp.__new__(AloudApp)
    app.root = _FakeRoot()

    app._on_player_state("playing")
    app._on_player_progress(2, 5)
    app._on_player_error(RuntimeError("boom"))

    assert len(app.root.after_calls) == 3
    assert all(delay == 0 for delay, _func, _args in app.root.after_calls)

    funcs = [func for _delay, func, _args in app.root.after_calls]
    assert app._apply_player_state in funcs
    assert app._apply_progress in funcs
    assert app._show_error in funcs

    args = [call_args for _delay, _func, call_args in app.root.after_calls]
    assert ("playing",) in args
    assert (2, 5) in args


# --- voice switching rebuilds the player, no display required --------------


class _FakeEngineForVoice:
    sample_rate = 16000

    def __init__(self, voice_path):
        self.voice_path = voice_path

    def synth(self, text, speed=1.0):
        return b""

    def close(self):
        pass


def test_ensure_player_rebuilds_when_voice_selection_changes(monkeypatch):
    from aloud.gui import AloudApp

    created_engines = []

    def fake_get_engine(name, model_path=None):
        engine = _FakeEngineForVoice(model_path)
        created_engines.append(engine)
        return engine

    monkeypatch.setattr("aloud.gui.get_engine", fake_get_engine)

    import threading

    app = AloudApp.__new__(AloudApp)
    app._engine = None
    app._engine_voice_path = None
    app._engine_cache_lock = threading.Lock()
    app._player = None
    app._player_voice_path = None
    app._on_player_state = lambda state: None
    app._on_player_progress = lambda index, total: None
    app._on_player_error = lambda exc: None

    selections = iter(["voice_a.onnx", "voice_a.onnx", "voice_b.onnx"])
    app._selected_voice_path = lambda: next(selections)

    player1 = app._ensure_player()
    player1_again = app._ensure_player()
    player2 = app._ensure_player()

    assert player1 is player1_again  # same voice selected twice -> reused
    assert player2 is not player1  # voice changed -> rebuilt
    assert len(created_engines) == 2
    assert created_engines[0].voice_path == "voice_a.onnx"
    assert created_engines[1].voice_path == "voice_b.onnx"
    # The player's engine is the latest one, wrapped for thread-safety
    # (playback and export threads share it).
    from aloud.tts.base import SynchronizedEngine

    assert isinstance(player2._engine, SynchronizedEngine)
    assert player2._engine._engine is created_engines[1]
