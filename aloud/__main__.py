"""Aloud CLI: python -m aloud <file> -o out.wav [--dump-text] [--voice PATH]
[--engine auto|piper|pyttsx3] [--speed FLOAT] [--quiet]
"""

import argparse
import os
import sys

from aloud.errors import AloudError
from aloud.extract import document_from_text, extract_text
from aloud.tts import get_engine
from aloud.tts.writer import (
    is_mp3_export_available,
    synthesize_to_mp3,
    synthesize_to_wav,
)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Turn argparse's default print-usage-then-sys.exit(2) into a
        # catchable error so main() always returns an int, never raises.
        raise AloudError(f"invalid arguments: {message}")


def _build_parser():
    parser = _ArgumentParser(
        prog="python -m aloud",
        description="Read PDF/TXT/DOCX/EPUB/HTML documents aloud using offline TTS.",
    )
    parser.add_argument(
        "file",
        help="Path to a .pdf, .txt, .docx, .epub, .html, or .htm file, "
        "or '-' to read plain text from stdin",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output audio file path (.wav always; .mp3 with the optional "
        "lameenc package)",
    )
    parser.add_argument(
        "--dump-text",
        action="store_true",
        help="Print extracted text to stdout and exit (no TTS engine loaded)",
    )
    parser.add_argument(
        "--voice", default=None, help="Path to a Piper .onnx voice model or directory"
    )
    parser.add_argument(
        "--engine", choices=["auto", "piper", "pyttsx3"], default="auto"
    )
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--quiet", action="store_true")
    return parser


def _run(args):
    if not args.dump_text and not args.output:
        raise AloudError("--output is required unless --dump-text is given")

    if args.file == "-":
        document = document_from_text(sys.stdin.read(), source="<stdin>")
    else:
        document = extract_text(args.file)

    if args.dump_text:
        print(document.full_text)
        return 0

    if args.speed <= 0:
        raise AloudError(f"--speed must be positive, got {args.speed}")

    if args.output.lower().endswith(".mp3"):
        if not is_mp3_export_available():
            raise AloudError(
                "MP3 output requires the optional 'lameenc' package "
                "(pip install -r requirements-optional.txt) - "
                "or export to .wav instead"
            )
        synthesize = synthesize_to_mp3
    else:
        synthesize = synthesize_to_wav

    engine = get_engine(args.engine, model_path=args.voice)
    try:
        def progress(done, total):
            print(f"chunk {done}/{total}", file=sys.stderr)

        try:
            result = synthesize(
                document.full_text,
                args.output,
                engine,
                speed=args.speed,
                progress=None if args.quiet else progress,
            )
        except BaseException:
            # Don't leave a truncated/corrupt WAV behind that looks like
            # a successful result after a mid-synthesis failure.
            if os.path.exists(args.output):
                os.remove(args.output)
            raise
    finally:
        engine.close()

    if not args.quiet:
        print(
            f"{args.output}: {result['chunks']} chunks, "
            f"{result['duration_sec']:.2f}s @ {result['sample_rate']}Hz"
        )
    return 0


def main(argv=None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        return _run(args)
    except SystemExit as exc:
        # argparse's built-in -h/--help action calls sys.exit() directly,
        # bypassing the overridden error() below (which only covers parse
        # failures). Convert it to a normal return so main() never raises.
        return exc.code if isinstance(exc.code, int) else 0
    except AloudError as exc:
        print(f"error: {exc.user_message.replace(chr(10), ' ')}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        detail = str(exc).replace("\n", " ")
        print(f"error: unexpected failure: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
