"""Tkinter GUI for Aloud.

Thread-safety: Player callbacks (on_state/on_progress/on_error) and the
Export worker thread fire on a background thread, and Tkinter is not
thread-safe. Every widget update they trigger is marshalled onto the Tk
main thread via root.after(0, ...); no callback touches a widget directly.
"""

import os
import pathlib
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from aloud.errors import AloudError
from aloud.extract import extract_text
from aloud.paths import models_dir
from aloud.player import Player, is_audio_available
from aloud.tts import get_engine
from aloud.tts.base import SynchronizedEngine
from aloud.tts.writer import is_mp3_export_available, synthesize_to_mp3, synthesize_to_wav

SPEED_MIN = 0.5
SPEED_MAX = 2.0
BROWSE_LABEL = "Browse..."


class AloudApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Aloud")

        self._document = None
        self._engine = None
        self._engine_voice_path = None
        # _get_engine is reached from both the Tk main thread (playback)
        # and the export worker thread; guard the cache itself, and wrap
        # the engine so concurrent synth() calls serialize too.
        self._engine_cache_lock = threading.Lock()
        self._player = None
        self._player_voice_path = None
        self._voice_paths = {}
        self._audio_available = is_audio_available()

        self._build_widgets()
        self._refresh_voice_list()

        if not self._audio_available:
            self._disable_playback_controls()
            self._set_status(
                "Playback unavailable (no audio device found) - "
                "you can still export to a file."
            )
        else:
            self._set_status("Ready.")

    # --- widget construction -------------------------------------------

    def _build_widgets(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=4)

        self.open_button = tk.Button(top, text="Open...", command=self._on_open)
        self.open_button.pack(side="left")

        tk.Label(top, text="Voice:").pack(side="left", padx=(12, 2))
        self.voice_combobox = ttk.Combobox(top, state="readonly", width=30)
        self.voice_combobox.pack(side="left")
        self.voice_combobox.bind("<<ComboboxSelected>>", self._on_voice_selected)

        self.text_widget = scrolledtext.ScrolledText(
            self.root, wrap="word", state="disabled", height=20
        )
        self.text_widget.pack(fill="both", expand=True, padx=8, pady=4)

        controls = tk.Frame(self.root)
        controls.pack(fill="x", padx=8, pady=4)

        self.play_button = tk.Button(controls, text="Play", command=self._on_play)
        self.play_button.pack(side="left")
        self.pause_button = tk.Button(
            controls, text="Pause", command=self._on_pause, state="disabled"
        )
        self.pause_button.pack(side="left", padx=(4, 0))
        self.stop_button = tk.Button(
            controls, text="Stop", command=self._on_stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(4, 0))

        tk.Label(controls, text="Speed:").pack(side="left", padx=(12, 2))
        self.speed_scale = tk.Scale(
            controls,
            from_=SPEED_MIN,
            to=SPEED_MAX,
            resolution=0.1,
            orient="horizontal",
            length=150,
        )
        self.speed_scale.set(1.0)
        self.speed_scale.pack(side="left")

        self.export_button = tk.Button(
            controls, text="Export...", command=self._on_export
        )
        self.export_button.pack(side="right")

        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(self.root, textvariable=self.status_var, anchor="w")
        self.status_label.pack(fill="x", padx=8, pady=(0, 4))

    # --- voice selection --------------------------------------------------

    def _refresh_voice_list(self):
        voice_dir = models_dir()
        onnx_files = sorted(voice_dir.glob("*.onnx")) if voice_dir.is_dir() else []
        self._voice_paths = {f.name: f for f in onnx_files}
        values = [f.name for f in onnx_files] + [BROWSE_LABEL]
        self.voice_combobox["values"] = values
        if onnx_files:
            self.voice_combobox.current(0)

    def _on_voice_selected(self, event=None):
        if self.voice_combobox.get() != BROWSE_LABEL:
            return
        path = filedialog.askopenfilename(
            title="Select a Piper voice model",
            filetypes=[("Piper voice model", "*.onnx")],
        )
        if not path:
            self.voice_combobox.set("")
            return
        chosen = pathlib.Path(path)
        self._voice_paths[chosen.name] = chosen
        values = list(self.voice_combobox["values"])
        if chosen.name not in values:
            values.insert(len(values) - 1, chosen.name)
            self.voice_combobox["values"] = values
        self.voice_combobox.set(chosen.name)

    def _selected_voice_path(self):
        selected = self.voice_combobox.get()
        chosen = self._voice_paths.get(selected)
        return str(chosen) if chosen is not None else None

    def _get_engine(self, voice_path=None):
        # voice_path is read from the widget by the caller when called from
        # a background thread (see _export_worker) - only the default,
        # main-thread-only case reads the combobox here directly.
        if voice_path is None:
            voice_path = self._selected_voice_path()
        with self._engine_cache_lock:
            if self._engine is None or self._engine_voice_path != voice_path:
                self._engine = SynchronizedEngine(
                    get_engine("auto", model_path=voice_path)
                )
                self._engine_voice_path = voice_path
            return self._engine

    def _get_speed(self):
        return float(self.speed_scale.get())

    # --- open / load --------------------------------------------------

    def _on_open(self):
        path = filedialog.askopenfilename(
            title="Open document",
            filetypes=[
                ("Supported documents", "*.pdf *.txt *.docx *.epub *.html *.htm *.tex *.latex")
            ],
        )
        if not path:
            return
        self.load_file(path)

    def load_file(self, path):
        """Extract text from `path` and populate the text widget.

        Extraction is fast (no network/heavy compute), so it runs
        synchronously on the main thread; only synthesis/export threads.
        """
        try:
            document = extract_text(path)
        except AloudError as exc:
            self._show_error(exc)
            return
        except Exception as exc:
            self._show_error(exc)
            return

        self._document = document
        self.text_widget.config(state="normal")
        self.text_widget.delete("1.0", "end")
        self.text_widget.insert("1.0", document.full_text)
        self.text_widget.config(state="disabled")
        self._set_status(f"Loaded {os.path.basename(path)} ({len(document.blocks)} blocks).")

    # --- playback --------------------------------------------------

    def _ensure_player(self):
        voice_path = self._selected_voice_path()
        if self._player is None or self._player_voice_path != voice_path:
            try:
                engine = self._get_engine(voice_path)
            except AloudError as exc:
                self._show_error(exc)
                return None
            if self._player is not None:
                self._player.stop()
            self._player = Player(engine)
            self._player.on_state = self._on_player_state
            self._player.on_progress = self._on_player_progress
            self._player.on_error = self._on_player_error
            self._player_voice_path = voice_path
        return self._player

    def _on_play(self):
        if self._document is None:
            messagebox.showinfo("Aloud", "Open a document first.")
            return
        player = self._ensure_player()
        if player is None:
            return
        if player.state == "paused":
            player.resume()
            return
        player.play(self._document.full_text, speed=self._get_speed())

    def _on_pause(self):
        if self._player is None:
            return
        if self._player.state == "playing":
            self._player.pause()
        elif self._player.state == "paused":
            self._player.resume()

    def _on_stop(self):
        if self._player is not None:
            self._player.stop()

    def _disable_playback_controls(self):
        self.play_button.config(state="disabled")
        self.pause_button.config(state="disabled")
        self.stop_button.config(state="disabled")

    # --- player callbacks (fire on the worker thread - marshal via after) --

    def _on_player_state(self, state):
        self.root.after(0, self._apply_player_state, state)

    def _apply_player_state(self, state):
        if not self._audio_available:
            return
        if state == "playing":
            self.play_button.config(state="disabled")
            self.pause_button.config(state="normal", text="Pause")
            self.stop_button.config(state="normal")
            self._set_status("Playing...")
        elif state == "paused":
            self.pause_button.config(text="Resume")
            self._set_status("Paused.")
        elif state in ("idle", "finished"):
            self.play_button.config(state="normal")
            self.pause_button.config(state="disabled", text="Pause")
            self.stop_button.config(state="disabled")
            self._set_status("Ready." if state == "idle" else "Finished.")

    def _on_player_progress(self, index, total):
        self.root.after(0, self._apply_progress, index, total)

    def _apply_progress(self, index, total):
        self._set_status(f"Chunk {index}/{total}")

    def _on_player_error(self, exc):
        self.root.after(0, self._show_error, exc)

    # --- export (own thread, buttons disabled while running) -----------

    def _on_export(self):
        if self._document is None:
            messagebox.showinfo("Aloud", "Open a document first.")
            return

        filetypes = [("WAV audio", "*.wav")]
        if is_mp3_export_available():
            filetypes.append(("MP3 audio", "*.mp3"))

        path = filedialog.asksaveasfilename(
            title="Export narration",
            defaultextension=".wav",
            filetypes=filetypes,
        )
        if not path:
            return

        # Capture widget state on the main thread now - the export worker
        # runs on a background thread and must never touch Tk widgets
        # directly, only via root.after(...).
        voice_path = self._selected_voice_path()
        speed = self._get_speed()

        self._set_export_controls_enabled(False)
        self._set_status("Exporting...")
        thread = threading.Thread(
            target=self._export_worker, args=(path, voice_path, speed), daemon=True
        )
        thread.start()

    def _export_worker(self, path, voice_path, speed):
        def progress(index, total):
            self.root.after(0, self._apply_progress, index, total)

        try:
            engine = self._get_engine(voice_path)
            if path.lower().endswith(".mp3") and is_mp3_export_available():
                result = synthesize_to_mp3(
                    self._document.full_text, path, engine, speed=speed, progress=progress
                )
            else:
                result = synthesize_to_wav(
                    self._document.full_text, path, engine, speed=speed, progress=progress
                )
        except AloudError as exc:
            self.root.after(0, self._export_failed, exc)
            return
        except Exception as exc:
            self.root.after(0, self._export_failed, exc)
            return

        self.root.after(0, self._export_finished, path, result)

    def _export_finished(self, path, result):
        self._set_export_controls_enabled(True)
        self._set_status(f"Exported {os.path.basename(path)} ({result['duration_sec']:.1f}s).")

    def _export_failed(self, exc):
        self._set_export_controls_enabled(True)
        self._set_status("Export failed.")
        self._show_error(exc)

    def _set_export_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.export_button.config(state=state)
        self.open_button.config(state=state)

    # --- shared helpers --------------------------------------------------

    def _set_status(self, message):
        self.status_var.set(message)

    def _show_error(self, exc):
        if isinstance(exc, AloudError):
            messagebox.showerror("Aloud", exc.user_message)
        else:
            messagebox.showerror(
                "Aloud", f"unexpected failure: {type(exc).__name__}: {exc}"
            )


def main() -> int:
    root = None
    try:
        root = tk.Tk()
        AloudApp(root)
    except tk.TclError as exc:
        print(f"error: cannot start GUI: {exc}", file=sys.stderr)
        print(
            "hint: the browser-based GUI works without a display server: "
            "python -m aloud.web",
            file=sys.stderr,
        )
        if root is not None:
            root.destroy()
        return 1
    except Exception as exc:
        print(f"error: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        if root is not None:
            root.destroy()
        return 1

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
