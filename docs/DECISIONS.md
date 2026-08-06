# Aloud — TTS & Audio Stack Decision Record

Date: 2026-08-06
Scope: TASK-001 research spike — lock the TTS engine and audio playback library before any application code is written.

All evidence below was produced by actually installing packages and running synthesis in a project-local virtualenv (`.venv/`) on this machine, not inferred from documentation alone.

---

## 1. Local environment

```
$ python3 --version
Python 3.13.11

$ uname -a
Linux syn-2603-6080-91f0-8290-0000-0000-0000-14dc.res6.spectrum.com 7.1.5-101.fc43.x86_64 #1 SMP PREEMPT_DYNAMIC Tue Jul 28 14:24:13 UTC 2026 x86_64 GNU/Linux
```

Fedora-based Linux, x86_64, Python 3.13.11. A project-local venv was created at `.venv/` for all installs (`python3 -m venv .venv`, pip upgraded 25.3 → 26.2.1).

---

## 2. TTS engine decision: **Piper TTS confirmed as primary**

**Package:** `piper-tts` on PyPI, version **1.6.0**, installs cleanly on Python 3.13.11.

```
$ pip install piper-tts
Successfully installed flatbuffers-25.12.19 numpy-2.5.1 onnxruntime-1.28.0 \
  packaging-26.3 pathvalidate-3.3.1 piper-tts-1.6.0 protobuf-7.35.1

$ pip show piper-tts
Name: piper-tts
Version: 1.6.0
Requires: onnxruntime, pathvalidate
```

- **Python API**, not subprocess-only: `from piper.voice import PiperVoice`.
- **API surface note (found only by running it, not in docs assumptions):** the obvious-looking method `voice.synthesize(text, wav_file)` does **not** write a valid WAV — it returns an `Iterable[AudioChunk]` of raw audio chunks and raises `wave.Error: # channels not specified` if you feed it directly into a `wave.Wave_write`. The correct method for writing a complete WAV file is:
  ```python
  voice.synthesize_wav(text, wav_file, syn_config=None, set_wav_format=True, include_alignments=False)
  ```
  This must be used (not `synthesize`) in the `tts/` layer implementation.
- **Dependency cost:** pulls in `onnxruntime` (~19 MB wheel), `numpy`, `protobuf`, `flatbuffers`. No native system libraries required beyond these Python wheels — Piper itself needed no `apt`/`dnf` packages.
- **License:** the `piper-tts` package is **GPL-3.0-or-later**. This is a package license, separate from voice-model licenses (see below). Fine for this project's stated free/offline use; flag if ever redistributed under a different license.

**Voice model:** `rhasspy/piper-voices` on Hugging Face, repo-level license **MIT**. Voice used: `en_US-lessac-medium` (general-purpose English, medium quality).

Download URLs (both returned HTTP 200, redirected through HF's signed CDN, no unexpected 404/redirect):
```
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx?download=true
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json?download=true
```

Downloaded into `models/`:
```
$ ls -la models/
-rw-r--r--. 63201294 en_US-lessac-medium.onnx        (~60.2 MB)
-rw-r--r--.     4885 en_US-lessac-medium.onnx.json
```

**Sample rate:** 22050 Hz (confirmed both in the voice's `.onnx.json` config and in the actual synthesized WAV header — see §5).

### End-to-end proof (§5 below has full detail)

```python
from piper.voice import PiperVoice
import wave

voice = PiperVoice.load("models/en_US-lessac-medium.onnx")
with wave.open("models/test_output.wav", "wb") as wav_file:
    voice.synthesize_wav("Aloud test synthesis.", wav_file)
```
Result: `OK - synthesis completed`, valid WAV produced. See §5 for the wave-module readback proof.

---

## 3. Fallback engine: **pyttsx3 — installs, but is non-functional on this machine**

```
$ pip install pyttsx3
Successfully installed pyttsx3-2.99
```

Installs cleanly (pure-Python wheel, no extra Python deps). However, at runtime:

```
$ which espeak-ng espeak
(no output — exit 127, neither binary found in PATH)

$ python -c "import pyttsx3; pyttsx3.init()"
RuntimeError: This means you probably do not have eSpeak or eSpeak-ng installed!
(raised in pyttsx3/drivers/_espeak.py:61, inside pyttsx3.init() itself,
 before save_to_file() is ever reached)
```

**Verdict:** `pyttsx3` depends on the native `espeak-ng` (or `espeak`) system binary/shared library, which is **not present on this machine** and cannot be installed via `pip` — it requires a system package manager (`sudo dnf install espeak-ng` on this Fedora-based host) which is outside the scope of what this project's `requirements.txt`/`pip install` can guarantee, and outside this task's authority to run (`sudo` not exercised here). No fallback WAV could be produced.

**Consequence for the architecture:** pyttsx3 cannot be treated as a zero-setup fallback. It should be documented as **optional** and **conditional on a system package** (`espeak-ng`) being present; the app should detect its absence at runtime and degrade gracefully (e.g. disable the fallback option and point the user at their OS package manager) rather than assuming it always works. Piper TTS remains the only engine that is proven to work standalone via `pip install` alone on this machine.

---

## 4. Audio playback library decision: **sounddevice — installs, but native PortAudio lib missing here (same class of issue as §3)**

```
$ pip install sounddevice
Successfully installed cffi-2.1.1 pycparser-3.0 sounddevice-0.5.5

$ python -c "import sounddevice; print(sounddevice.query_devices())"
OSError: PortAudio library not found
(raised in sounddevice.py:72, at import-time use)
```

`sounddevice` (v0.5.5) itself is the correct *choice* — its API (`sd.play(array, samplerate)` / `sd.stop()`) directly satisfies the "stop mid-stream" requirement with a two-call interface, and unlike `simpleaudio` it is actively maintained (Jan 2026 release) with no known Python 3.12+/3.13 build breakage. `simpleaudio` was **not installed/tested** here because its own PyPI metadata still only declares Python 3.3–3.6 support (last release 2019-11-29) — installing it would risk exactly the build failure this spike was meant to avoid, so it's ruled out on that evidence alone without needing to burn an install attempt.

Like `pyttsx3`/`espeak-ng`, `sounddevice` needs a **native system library** (PortAudio, e.g. `libportaudio2` / `portaudio` package) that is not present on this machine and not installable via pip. This is expected and normal for any Python audio-playback binding (pygame.mixer would hit the same class of native-dependency requirement via SDL2/SDL_mixer).

**Decision stands: `sounddevice` is the playback library**, with the explicit caveat that the target deployment environment (the user's actual desktop machine, not necessarily this sandboxed dev host) must have PortAudio available — either already present (common on desktop Linux distros with audio, macOS, Windows) or installed via one line in the README (`sudo dnf install portaudio` / `sudo apt install libportaudio2`). This should be called out clearly in the project README as a runtime prerequisite, and the GUI/CLI should catch `OSError` from `sounddevice` at startup and give a clear error message rather than crashing uninformatively.

---

## 5. End-to-end proof (full evidence)

Full synthesis run, in the project venv, using the real `synthesize_wav` API found in step 2:

```python
from piper.voice import PiperVoice
import wave

voice = PiperVoice.load("models/en_US-lessac-medium.onnx")
with wave.open("models/test_output.wav", "wb") as wav_file:
    voice.synthesize_wav("Aloud test synthesis.", wav_file)
# -> OK - synthesis completed
```

Readback via stdlib `wave` (no external tools needed):
```python
import wave
w = wave.open("models/test_output.wav", "rb")
print("channels:", w.getnchannels(), "sampwidth:", w.getsampwidth(),
      "framerate:", w.getframerate(), "nframes:", w.getnframes(),
      "duration_sec:", w.getnframes()/w.getframerate())
```
Output:
```
channels: 1 sampwidth: 2 framerate: 22050 nframes: 35072 duration_sec: 1.5905668934240362
```

File: `models/test_output.wav`, 70,188 bytes, mono, 16-bit PCM, 22050 Hz, ~1.59s for the sentence "Aloud test synthesis." — a real, non-empty, non-zero-duration WAV.

Voice model download command used:
```
curl -L -o models/en_US-lessac-medium.onnx "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx?download=true"
curl -L -o models/en_US-lessac-medium.onnx.json "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json?download=true"
```
Model file: `models/en_US-lessac-medium.onnx` — 63,201,294 bytes (~60.2 MB). Config: `models/en_US-lessac-medium.onnx.json` — 4,885 bytes.

---

## Pinned candidate list for `requirements.txt`

Versions below are exactly what was installed and proven working on this machine (`pip freeze` excerpt):

```
piper-tts==1.6.0
onnxruntime==1.28.0
numpy==2.5.1
protobuf==7.35.1
flatbuffers==25.12.19
pathvalidate==3.3.1
sounddevice==0.5.5
cffi==2.1.1
pycparser==3.0
pyttsx3==2.99
```

`pyttsx3` is pinned as an **optional** fallback dependency (works only if the OS provides `espeak-ng`/`espeak`); it should probably live in an `extras_require`/optional section rather than the hard-required list, with the app checking for it at runtime.

Not pinned / not adopted: `simpleaudio` (ruled out — stale, no Python 3.9+ support declared, high risk of build failure per its own PyPI metadata).

---

## No paid/account-gated resources

Confirmed: `piper-tts` (PyPI, free), `rhasspy/piper-voices` (Hugging Face, public, no auth/token needed — plain `curl`/HTTP GET succeeded), `pyttsx3` (PyPI, free), `sounddevice` (PyPI, free). No API keys, no paid tiers, no account gating anywhere in this stack.

---

## Open follow-up for later tasks

1. **System prerequisites must be documented in the README** before GUI/playback work starts: PortAudio (`sounddevice`) and, if the pyttsx3 fallback is kept, `espeak-ng`. Neither is present on this dev machine, so playback and the fallback engine remain untested end-to-end here — only Piper's file-based synthesis (no live audio device needed) was proven.
2. The `tts/` layer must use `voice.synthesize_wav(...)`, not `voice.synthesize(...)` — the latter returns raw chunks and will raise `wave.Error` if misused, as discovered during this spike.
3. Playback (`player/`) work should design for `sounddevice`'s `sd.play()`/`sd.stop()`/`sd.wait()` API, but its actual runtime behavior (device enumeration, stop-mid-stream) could not be verified on this sandboxed host due to missing PortAudio — recommend a targeted TESTER re-check once developed on/deployed to a machine with real audio hardware.

---

## Addendum (TASK-004): confirmed speed-control API

Inspecting the installed `piper-tts==1.6.0` package directly (`piper/config.py`, `piper/voice.py`) rather than guessing:

- Speed is controlled via `piper.config.SynthesisConfig(length_scale=...)`, passed as `voice.synthesize(text, syn_config=syn_config)` (the streaming API — `synthesize_wav` internally just iterates `synthesize()` and writes each `AudioChunk.audio_int16_bytes` to the wave file). Each `AudioChunk` also exposes `audio_int16_bytes` directly, which `aloud/tts/piper_engine.py` uses to get raw PCM without going through a WAV file.
- `length_scale` docstring: "< 1 is faster, > 1 is slower". Confirmed empirically on this machine, synthesizing `"Aloud test synthesis. Aloud test synthesis. Aloud test synthesis."`:
  ```
  length_scale=1.0   -> duration=5.248s
  length_scale=0.667 -> duration=3.564s   (i.e. speed=1.5 -> length_scale=1/1.5)
  length_scale=1.5   -> duration=6.931s
  ```
  Direction confirmed correct: lower `length_scale` -> shorter audio. `aloud/tts/piper_engine.py` maps `length_scale = 1.0 / speed`.
- End-to-end re-verification through the full `extract -> synthesize_to_wav` pipeline (TASK-004): `samples/sample.txt` -> 34.6s (6 chunks, 22050 Hz), `samples/sample_long.txt` -> 257.1s (26 chunks) — duration grows with input length as required. Same short text at `speed=1.0` -> 34.6s vs `speed=1.5` -> 26.3s — faster speed produces shorter audio, confirming the mapping holds through the full engine/writer stack, not just the raw Piper call.

---

## Addendum (TASK-008): MP3 export decision — `lameenc` adopted as optional

Attempted `pip install lameenc` in the project venv per the TASK-008 instruction to decide MP3 export support from real evidence, not assumption:

```
$ pip install lameenc
Collecting lameenc
  Downloading lameenc-1.8.4-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_34_x86_64.whl (247 kB)
Installing collected packages: lameenc
Successfully installed lameenc-1.8.4
```

Installed cleanly, pure pip wheel, no system package (no ffmpeg/libmp3lame needed at the OS level — the wheel bundles the LAME encoder). Smoke-tested actual encoding (not just import):

```python
import lameenc, struct
encoder = lameenc.Encoder()
encoder.set_bit_rate(128)
encoder.set_in_sample_rate(22050)
encoder.set_channels(1)
encoder.set_quality(2)
pcm = struct.pack('<' + 'h' * 11025, *([0] * 11025))  # 0.5s silence
mp3_data = encoder.encode(pcm) + encoder.flush()
```
Result: 9195 bytes produced, first bytes `b'\xff\xf3\xc0\xc4'` (a valid MPEG frame sync header). Verified with the `file` command: `MPEG ADTS, layer III, v2, 128 kbps, 22.05 kHz, Monaural` — a genuinely valid, playable MP3, not just non-empty output.

**Decision: MP3 export is supported, as an optional feature.** `lameenc==1.8.4` is pinned in `requirements-optional.txt`, not `requirements.txt` — the GUI checks `aloud.tts.writer.is_mp3_export_available()` before offering `.mp3` as an export choice, and WAV export always works regardless (no dependency on `lameenc`). No ffmpeg or other non-pip dependency was introduced, per the constraint.
