"""Piper voice catalog + downloader (rhasspy/piper-voices on Hugging Face).

This is the ONLY module in Aloud that touches the network, and only when
the user explicitly lists or downloads voices - synthesis and playback stay
fully offline. Voices are free, MIT-licensed, and need no account.

CLI:
    python -m aloud.voices list [FILTER]     # e.g. `list german`
    python -m aloud.voices download VOICE_ID # e.g. `download en_GB-alba-medium`
"""

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

from aloud.errors import AloudError
from aloud.paths import models_dir as default_models_dir

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
CATALOG_URL = f"{BASE_URL}/voices.json"

_NETWORK_HINT = (
    "check your internet connection (the network is only needed to download "
    "voices - reading and playback are fully offline)"
)
_DOWNLOAD_CHUNK = 256 * 1024


def voice_repo_dir(voice_id):
    """Map 'en_US-lessac-medium' -> 'en/en_US/lessac/medium'."""
    try:
        locale, rest = voice_id.split("-", 1)
        name, quality = rest.rsplit("-", 1)
    except ValueError:
        raise AloudError(
            f"Invalid voice id '{voice_id}' (expected the form "
            "<locale>-<name>-<quality>, e.g. en_US-lessac-medium)"
        )
    if not locale or not name or not quality:
        raise AloudError(
            f"Invalid voice id '{voice_id}' (expected the form "
            "<locale>-<name>-<quality>, e.g. en_US-lessac-medium)"
        )
    family = locale.split("_")[0]
    return f"{family}/{locale}/{name}/{quality}"


def installed_voices(models_dir=None):
    models_dir = pathlib.Path(models_dir) if models_dir else default_models_dir()
    if not models_dir.is_dir():
        return set()
    return {f.stem for f in models_dir.glob("*.onnx")}


def fetch_catalog(timeout=30):
    """Fetch and normalize voices.json. Returns a list of dicts sorted by id."""
    try:
        with urllib.request.urlopen(CATALOG_URL, timeout=timeout) as response:
            raw = json.load(response)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise AloudError(f"Could not fetch the voice catalog - {_NETWORK_HINT}") from exc

    voices = []
    for key, info in raw.items():
        if not isinstance(info, dict):
            continue
        language = info.get("language", {}) or {}
        size_bytes = sum(
            f.get("size_bytes", 0) for f in (info.get("files", {}) or {}).values()
        )
        voices.append(
            {
                "id": key,
                "language": language.get("name_english", "?"),
                "language_code": language.get("code", "?"),
                "quality": info.get("quality", "?"),
                "size_mb": round(size_bytes / (1024 * 1024)) if size_bytes else None,
                "num_speakers": info.get("num_speakers", 1),
            }
        )
    voices.sort(key=lambda v: (v["language_code"], v["id"]))
    return voices


def download_voice(voice_id, models_dir=None, progress=None, timeout=60):
    """Download <id>.onnx and <id>.onnx.json into models_dir.

    progress, if given, is called as progress(filename, received_bytes,
    total_bytes) where total_bytes may be None if the server didn't say.
    Files land via a .part temp name + os.replace, so an interrupted
    download never leaves a truncated model that looks installed.
    """
    repo_dir = voice_repo_dir(voice_id)
    models_dir = pathlib.Path(models_dir) if models_dir else default_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)

    file_names = [f"{voice_id}.onnx", f"{voice_id}.onnx.json"]
    for file_name in file_names:
        url = f"{BASE_URL}/{repo_dir}/{file_name}?download=true"
        final_path = models_dir / file_name
        part_path = models_dir / (file_name + ".part")
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                total = response.headers.get("Content-Length")
                total = int(total) if total else None
                received = 0
                with open(part_path, "wb") as out_file:
                    while True:
                        block = response.read(_DOWNLOAD_CHUNK)
                        if not block:
                            break
                        out_file.write(block)
                        received += len(block)
                        if progress is not None:
                            progress(file_name, received, total)
        except urllib.error.HTTPError as exc:
            part_path.unlink(missing_ok=True)
            if exc.code == 404:
                raise AloudError(
                    f"Voice '{voice_id}' was not found in the catalog - run "
                    "'python -m aloud.voices list' to see available voice ids"
                ) from exc
            raise AloudError(
                f"Download of {file_name} failed (HTTP {exc.code}) - {_NETWORK_HINT}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            part_path.unlink(missing_ok=True)
            raise AloudError(f"Download of {file_name} failed - {_NETWORK_HINT}") from exc
        os.replace(part_path, final_path)

    return [str(models_dir / name) for name in file_names]


# --- CLI ---------------------------------------------------------------------


def _cmd_list(args):
    catalog = fetch_catalog()
    installed = installed_voices(args.models_dir)
    needle = (args.filter or "").lower()
    shown = 0
    for voice in catalog:
        haystack = f"{voice['id']} {voice['language']} {voice['language_code']}".lower()
        if needle and needle not in haystack:
            continue
        shown += 1
        mark = "*" if voice["id"] in installed else " "
        size = f"{voice['size_mb']} MB" if voice["size_mb"] else "?"
        print(
            f"{mark} {voice['id']:<40} {voice['language']:<22} "
            f"{voice['quality']:<8} {size}"
        )
    if shown == 0:
        print(f"No voices match '{args.filter}'.")
    else:
        print(f"\n{shown} voices ('*' = already installed in {args.models_dir}).")
        print("Install one with: python -m aloud.voices download <id>")
    return 0


def _cmd_download(args):
    installed = installed_voices(args.models_dir)
    if args.voice_id in installed:
        print(f"Voice '{args.voice_id}' is already installed in {args.models_dir}.")
        return 0

    def progress(file_name, received, total):
        if total:
            percent = received * 100 // total
            sys.stderr.write(f"\r{file_name}: {percent}% ({received // (1024*1024)} MB)")
        else:
            sys.stderr.write(f"\r{file_name}: {received // (1024*1024)} MB")
        sys.stderr.flush()
        if total and received >= total:
            sys.stderr.write("\n")

    paths = download_voice(args.voice_id, models_dir=args.models_dir, progress=progress)
    print(f"Installed voice '{args.voice_id}':")
    for path in paths:
        print(f"  {path}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aloud.voices",
        description="List and download free Piper voices (one-time network use).",
    )
    parser.add_argument(
        "--models-dir",
        default=None,
        help=f"Where voice models live (default: {default_models_dir()})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List available voices")
    list_parser.add_argument(
        "filter", nargs="?", default="", help="Only show voices matching this text"
    )

    download_parser = subparsers.add_parser("download", help="Download a voice by id")
    download_parser.add_argument("voice_id", help="e.g. en_US-lessac-medium")

    try:
        args = parser.parse_args(argv)
        if args.models_dir is None:
            args.models_dir = default_models_dir()
        if args.command == "list":
            return _cmd_list(args)
        return _cmd_download(args)
    except AloudError as exc:
        print(f"error: {exc.user_message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
