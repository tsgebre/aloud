# Aloud

Aloud reads your PDF, EPUB, TXT, DOCX, HTML, and LaTeX documents aloud, entirely
on your own computer — no cloud account, no subscription, no internet
connection needed once it's set up. Point it at a document, and it turns the text into a
natural-sounding narration you can listen to or save as an audio file.

It comes with a polished browser-based GUI (recommended), a simple Tkinter
desktop window, and a command-line tool (CLI) for scripting.

## Setup

Requires Python 3.10+ (the code uses PEP 604 `X | None` union type
annotations, e.g. in `aloud/extract/base.py`, which need 3.10 at minimum).
Developed and actually tested on Python 3.13.11, Linux — versions between
3.10 and 3.13 are expected to work but have not been verified here.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or install Aloud as a package (adds the `aloud`, `aloud-web`, `aloud-gui`,
and `aloud-voices` commands to your PATH — `pipx install .` works too):

```sh
pip install .            # extras: .[mp3] MP3 export, .[qr] LAN-mode QR code
```

When installed as a package (rather than run from this checkout), voice
models live in `~/.local/share/aloud/models` instead of `./models`; set
`$ALOUD_MODELS_DIR` to override either way.

### Download a voice (one-time step)

Aloud's default voice engine (Piper) needs one voice model downloaded once.
After that, everything runs fully offline:

```sh
python -m aloud.voices download en_US-lessac-medium
```

Voices come from the public [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices)
Hugging Face repository — free, MIT-licensed, no account or API key
required. A typical medium-quality voice is about 60 MB. Browse the full
catalog (dozens of languages) with:

```sh
python -m aloud.voices list            # everything
python -m aloud.voices list german     # filter by language or name
```

The browser GUI has the same thing built in: the **＋ Add voice** button
(under ⚙ settings) lists the catalog with search, and downloads straight
into `models/`.

<details>
<summary>Manual alternative (curl)</summary>

```sh
mkdir -p models
curl -L -o models/en_US-lessac-medium.onnx "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx?download=true"
curl -L -o models/en_US-lessac-medium.onnx.json "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json?download=true"
```

Aloud picks up any `.onnx`/`.json` pair placed in `models/`.
</details>

## System prerequisites

- **Live playback in the Tkinter GUI** (Play/Pause/Stop, via `sounddevice`)
  needs the **PortAudio** library installed at the OS level. The browser GUI
  (`python -m aloud.web`) does *not* need PortAudio — the browser plays the
  audio itself.
  - Debian/Ubuntu: `sudo apt install libportaudio2`
  - Fedora: `sudo dnf install portaudio`

  If PortAudio isn't installed, Aloud does **not** crash: the GUI detects
  this at startup, disables the playback buttons, and tells you why —
  everything else (extraction, the CLI, WAV/MP3 export) still works
  perfectly without it. Live playback has not been exercised on real audio
  hardware during this project's development (the development machine has
  no PortAudio installed) — only its clean-degradation path is verified.
- **`espeak-ng`** is only needed for the *optional* `pyttsx3` fallback voice
  (see below). Piper, the default engine, needs no system packages at all.

## Optional extras

```sh
pip install -r requirements-optional.txt
```

This adds:
- **`lameenc`** — enables MP3 export in the GUI's Export dialog (in
  addition to WAV, which is always available). Pure Python wheel, no
  system dependency.
- **`pytesseract` + `pillow`** — enables OCR for scanned/image-only PDFs.
  Also requires the system **`tesseract`** binary
  (`sudo dnf install tesseract` / `sudo apt install tesseract-ocr`).
  Without it, scanned PDFs are rejected with a clean error naming exactly
  what to install.
- **`qrcode`** — prints a scannable QR code for the phone-friendly link when
  the browser GUI runs in LAN mode (`python -m aloud.web --host 0.0.0.0`).
  Pure Python wheel, no system dependency; without it the link is printed
  as plain text.
- **`pyttsx3`** — an offline fallback voice using the system `espeak-ng`.
  Only used if you explicitly request `--engine pyttsx3`, or if Piper
  itself is unavailable. Requires `espeak-ng`/`espeak` to be installed at
  the OS level (`sudo apt install espeak-ng` / `sudo dnf install espeak-ng`);
  without it, Aloud reports a clean error naming `espeak-ng` rather than
  crashing.

## CLI usage

```
python -m aloud <file> -o <out.wav|out.mp3> [--dump-text] [--voice PATH]
                 [--engine auto|piper|pyttsx3] [--speed FLOAT] [--quiet]
```

```
$ python -m aloud --help
usage: python -m aloud [-h] [-o OUTPUT] [--dump-text] [--voice VOICE]
                       [--engine {auto,piper,pyttsx3}] [--speed SPEED]
                       [--quiet]
                       file

Read PDF/TXT/DOCX/EPUB/HTML/LaTeX documents aloud using offline TTS.

positional arguments:
  file                  Path to a .pdf, .txt, .docx, .epub, .html, .htm, .tex,
                        or .latex file, or '-' to read plain text from stdin

options:
  -h, --help            show this help message and exit
  -o, --output OUTPUT   Output audio file path (.wav always; .mp3 with the
                        optional lameenc package)
  --dump-text           Print extracted text to stdout and exit (no TTS engine
                        loaded)
  --voice VOICE         Path to a Piper .onnx voice model or directory
  --engine {auto,piper,pyttsx3}
  --speed SPEED
  --quiet
```

Worked examples:

```sh
# Just extract and print the text (no voice model needed at all):
$ python -m aloud samples/sample.txt --dump-text
Aloud is a small offline text-to-speech reader built for PDF, TXT, and DOCX documents. ...
The quick brown fox jumps over the lazy dog.
...

# Synthesize to a WAV file, with progress on stderr:
$ python -m aloud samples/sample.txt -o out.wav
chunk 1/6
chunk 2/6
...
chunk 6/6
out.wav: 6 chunks, 35.09s @ 22050Hz

# Same, but quiet (only the final summary line, useful for scripting):
$ python -m aloud samples/sample.txt -o out.wav --quiet

# 1.5x speed:
$ python -m aloud samples/sample.txt -o out.wav --speed 1.5

# A specific voice model:
$ python -m aloud samples/sample.txt -o out.wav --voice models/en_US-lessac-medium.onnx

# MP3 output (needs the optional lameenc package):
$ python -m aloud samples/sample.txt -o out.mp3

# Read piped/pasted text from stdin:
$ echo "Any text at all." | python -m aloud - -o out.wav
$ python -m aloud - --dump-text < notes.txt
```

Exit codes:

| Code | Meaning |
|------|---------|
| `0`   | Success. |
| `2`   | A known, expected error (bad arguments, unsupported file, missing/encrypted/corrupt/scanned document, missing voice model). A one-line `error: ...` message is printed to stderr. |
| `1`   | An unexpected failure. A one-line `error: unexpected failure: <type>: <message>` is printed to stderr — never a raw traceback. |
| `130` | Interrupted with Ctrl-C. |

Examples of the error path:

```sh
$ python -m aloud samples/sample.txt
error: --output is required unless --dump-text is given

$ python -m aloud samples/corrupt.pdf -o out.wav
error: Could not read PDF file: samples/corrupt.pdf

$ python -m aloud notes.xyz -o out.wav   # assuming notes.xyz exists
error: Unsupported file format '.xyz'. Supported formats: .docx, .epub, .htm, .html, .latex, .pdf, .tex, .txt
```

## GUI usage (browser, recommended)

```sh
python -m aloud.web
```

This starts a local-only server (bound to `127.0.0.1`, stdlib `http.server` —
no new dependencies, still fully offline) and opens the GUI in your default
browser. Because the *browser* plays the audio, live playback works even
without PortAudio, and the GUI itself works on Wayland, over SSH port
forwarding, or anywhere else Tkinter's X11 requirement is a problem.

- **Open a document** — drag-and-drop a `.pdf`/`.epub`/`.txt`/`.docx`/
  `.html`/`.tex` anywhere on the page, or click the drop zone / **Open...**
  button. For a multi-file LaTeX project (`main.tex` with `\input`/
  `\include`d section files), select or drop **all the files together** —
  the browser can't see files you didn't hand it, so the section files
  must be part of the selection. Aloud figures out which one is the main
  file and assembles the full document.
- **Paste text** — the **✏ Paste text** button below the drop zone opens a
  dialog, so you can listen to any text without saving a file first.
- **Open a URL** — type or paste a web address into the URL box below the
  drop zone and Aloud fetches the page and reads it (works for online PDFs
  and `.txt` files too, by Content-Type). This is the one feature that goes
  online besides voice downloads, and only ever for a URL you typed
  yourself — everything else stays fully offline. Heavily script-rendered
  pages may extract poorly; for those, copy the text and use *paste text*
  instead.
- **Read aloud** — press the big **play/pause** button (or the spacebar);
  the sentence currently being read is highlighted and kept in view, with
  the current sentence number and estimated listening time remaining shown
  above the controls. Click any sentence to start reading from there, step
  sentence-by-sentence with the ⏮/⏭ buttons (arrow keys work too), or
  **click anywhere on the progress bar** to jump straight there. Press
  `?` for a keyboard-shortcut overview. On phones and tablets, the play
  bar also hooks into the system media controls (lock screen / headphone
  buttons).
- **Sleep timer** — pause playback automatically after 15/30/45/60 minutes,
  for listening in bed; a live countdown shows while it's armed.
- **Resume where you left off** — Aloud remembers your position per
  document (and your speed/voice/text-size/theme choices) in the browser's
  local storage; reopen the same file and a banner offers **Resume** at the
  sentence you stopped at, or **Start over**.
- **Speed** — 0.5x–2.0x, applied live during playback (no re-synthesis) and
  to exports.
- **Settings (⚙ in the header)** — pick any `.onnx` voice found in
  `models/` (shown with friendly names), click **＋ Add voice** to browse
  and download more (many languages) from the free Piper catalog without
  leaving the app, adjust the reading text size with `A−` / `A+`, and
  switch the theme between Auto (follow the system), Light, and Dark.
- **Export (⋯ menu in the play bar)** — download the full narration as WAV
  (always) or MP3 (if `lameenc` is installed), with sentence-level progress
  and a Cancel button for long documents.

Options: `--port N` (default: pick a free port), `--no-browser` (print the
URL only), `--host ADDR` (see below). By default the server is per-run
token-protected and only listens on loopback.

### Listening on your phone (LAN mode)

```sh
python -m aloud.web --host 0.0.0.0
```

This lets other devices on your local network — a phone on the couch, a
tablet in bed — use the full GUI in their own browser while your computer
does the synthesizing. At startup Aloud prints a link containing a per-run
access token, plus a QR code to scan if the optional `qrcode` package is
installed (`pip install qrcode`; without it the link is still printed as
text):

```
Aloud is running at http://127.0.0.1:34787/?t=Kq3...
On a phone or another device on this network, open:
  http://192.168.1.23:34787/?t=Kq3...
█▀▀▀▀▀█ ... (scan me)
Note: anyone on your network with this exact link can use Aloud while it runs.
```

In LAN mode the page itself requires the token (the `?t=...` part of the
link), so other devices on the network can't use Aloud without the full
link — keep the whole URL when bookmarking. Use `--host <address>` with one
specific interface address to listen on just that network. The token
changes every run, and playback position still syncs per device (it lives
in each browser's local storage).

## GUI usage (Tkinter desktop window)

```sh
python -m aloud.gui
```

Requires a working X11 display (on Wayland, this means XWayland). If you
see `error: cannot start GUI: no display name and no $DISPLAY environment
variable`, use `python -m aloud.web` instead.

- **Open...** — pick a `.pdf`/`.txt`/`.docx`/`.epub`/`.html`/`.tex` file;
  its extracted text appears in the reading pane.
- **Voice** — choose any `.onnx` voice model found in `models/`, or
  **Browse...** to pick one elsewhere.
- **Play / Pause / Stop** — narrate the loaded document. Disabled with an
  explanatory status message if no audio device (PortAudio) is available.
- **Speed** — 0.5x–2.0x, applied to both playback and export.
- **Export...** — write the full narration to a `.wav` file (always
  available) or `.mp3` file (only offered if `lameenc` is installed). Runs
  in the background; the window stays responsive and shows progress in the
  status line.

If no display is available (e.g. running over SSH with no X server), Aloud
prints one clean line and exits rather than crashing:

```
$ python -m aloud.gui
error: cannot start GUI: no display name and no $DISPLAY environment variable
```

## Why Piper?

Aloud defaults to [Piper](https://github.com/rhasspy/piper) as its TTS
engine, chosen after a research spike (see `docs/DECISIONS.md`) that
weighed it against alternatives against this project's constraints (free,
fully offline after setup, no paid or account-gated service):

- **Free and offline**: pure `pip install piper-tts`, no account, no API
  key, no network calls at synthesis time. Voice models are free
  (MIT-licensed) downloads from a public Hugging Face repository.
- **No system packages required**: unlike `pyttsx3`, Piper needs nothing
  installed at the OS level — everything comes through pip wheels
  (`onnxruntime`, `numpy`, etc.).
- **Neural voice quality**: Piper uses a small neural (VITS-based) model,
  which sounds substantially more natural than the formant-based `espeak-ng`
  engine `pyttsx3` wraps.
- **Cloud TTS was ruled out entirely**: services like AWS Polly, Google
  Cloud TTS, or ElevenLabs would violate the free/offline/no-account
  requirement outright — they're paid, require an account, and need a
  network connection at synthesis time.

`pyttsx3`/`espeak-ng` is kept only as an **optional fallback** — it's
noticeably more robotic-sounding, but it's a reasonable last resort if
Piper can't be installed on a given machine, and it needs a system package
(`espeak-ng`) that Piper doesn't.

## Running the tests

```sh
pip install -r requirements-dev.txt
python -m pytest -q
```

(Use `python -m pytest` when running from the checkout without installing —
`python -m` is what puts the project on `sys.path`. After `pip install -e .`
the bare `pytest` command works too. `requirements-dev.txt` provides
`pytest` itself, plus `fpdf2`, which is only needed if you regenerate the
fixtures under `samples/` via `tools/make_samples.py`.)

Some tests skip cleanly rather than fail, depending on what's available on
the machine running them:
- Tests needing the real Piper voice model skip if `models/*.onnx` is
  absent (run the download step above first to enable them).
- GUI tests needing a real Tk window skip if there's no `$DISPLAY` (e.g. a
  headless CI runner or SSH session with no X server).
- The `pyttsx3`/`SoundDeviceBackend` error-path tests skip if `espeak-ng` /
  PortAudio actually *are* present and working, since those tests are
  specifically about the graceful-failure path.

On a fully-equipped machine (voice model downloaded, display present,
PortAudio and espeak-ng installed), every test should run rather than skip.

## Limitations

- **OCR is optional, not built in.** Scanned/image-only PDFs work only if
  the OCR extras are installed (see **Optional extras**); text-layer
  extraction is always preferred when present.
- **No RTF or plain-Markdown** support — supported formats are PDF, EPUB,
  TXT, DOCX, HTML, and LaTeX (`.tex`/`.latex`).
- **LaTeX is read as prose, not typeset output.** The `.tex` source is
  stripped in pure Python: sections become headings, text-styling macros
  keep their text, and figures/tables reduce to their captions.
  Cross-references are resolved to their numbers by replaying LaTeX's
  counters (`Section~\ref{sec:x}` → "Section 2.1", `\eqref` → "(3)",
  `\autoref`/`\cref` supported); undefined labels are dropped.
  Multi-file projects are assembled: `\input`/`\include`/`\subfile`
  files are spliced in (relative to the main file's folder; in the
  browser GUI, upload the main file and its section files together)
  and sections/references number correctly across files. Inline
  math is read literally (`$E=mc^2$` → "E=mc^2"), while display math,
  tables, and code listings are skipped — there is no spoken form for
  them. For math-heavy papers, compiling to PDF first may narrate better.
- **No DRM-protected files** — encrypted/password-protected PDFs and
  DRM-protected EPUBs are detected and rejected with a clear error, not
  decrypted.
- **English by default.** Voices for dozens of other languages are a
  one-line download away (`python -m aloud.voices list`, then
  `download <id>`, or the GUI's **＋ Add voice** button), but none are
  bundled.

## Troubleshooting

| Symptom | What you'll see | What to do |
|---|---|---|
| No voice model downloaded | `error: No Piper voice model (.onnx) found in .../models. Download one first - see docs/DECISIONS.md for the exact curl command ...` | Run `python -m aloud.voices download en_US-lessac-medium` (or use the GUI's **＋ Add voice** button, under ⚙ settings). |
| No display (Tkinter GUI: Wayland without XWayland, SSH, etc.) | `error: cannot start GUI: no display name and no $DISPLAY environment variable` | Use the browser GUI instead: `python -m aloud.web`. It needs no display server at all. |
| No audio device / PortAudio missing | GUI shows: *"Playback unavailable (no audio device found) - you can still export to a file."* Play/Pause/Stop are disabled. | Install PortAudio (see **System prerequisites**) if you want live playback; export and the CLI work regardless. |
| Encrypted/password-protected PDF | `error: PDF is password-protected: <path>` | Remove the password protection first (Aloud won't attempt to decrypt it). |
| Scanned/image-only PDF | `error: No extractable text found in <path> (scanned/image-only PDFs need OCR - install the system 'tesseract' package plus 'pip install pytesseract pillow' to enable it)` | Install the OCR extras (see **Optional extras**) and retry — Aloud will then OCR the page images itself. |
| Unsupported file extension | `error: Unsupported file format '.xyz'. Supported formats: .docx, .epub, .htm, .html, .latex, .pdf, .tex, .txt` | Convert the file to one of the supported formats. |
| `pyttsx3` fallback requested but unavailable | `error: pyttsx3 fallback is unavailable: the system 'espeak-ng' (or 'espeak') package is not installed. ...` | Install `espeak-ng` (see **Optional extras**), or just use the default Piper engine. |
