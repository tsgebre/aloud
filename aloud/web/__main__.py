"""Entry point: `python -m aloud.web` starts the browser GUI."""

import argparse
import sys
import threading
import webbrowser

from aloud.web import create_server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aloud.web",
        description="Start Aloud's browser-based GUI on 127.0.0.1 (local only).",
    )
    parser.add_argument(
        "--port", type=int, default=0, help="Port to listen on (default: pick a free one)"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Don't open the browser automatically"
    )
    args = parser.parse_args(argv)

    try:
        server = create_server(port=args.port)
    except OSError as exc:
        print(f"error: cannot start server: {exc}", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Aloud is running at {url}", flush=True)
    print("Press Ctrl-C to quit.", flush=True)
    if not args.no_browser:
        # Slight delay so the server is accepting before the browser connects.
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
