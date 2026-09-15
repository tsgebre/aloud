"""Entry point: `python -m aloud.web` starts the browser GUI."""

import argparse
import socket
import sys
import threading
import webbrowser

from aloud.web import LOOPBACK_HOSTS, create_server


def _lan_ip(bind_host):
    """Best-guess address other devices on the network can reach us at."""
    if bind_host not in ("0.0.0.0", "::", ""):
        return bind_host  # bound to one specific interface: that's the address
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # Routing lookup only - connect() on a UDP socket sends no packet.
            probe.connect(("192.0.2.1", 80))
            return probe.getsockname()[0]
    except OSError:
        return None


def _print_qr(url):
    """Print a scannable QR code for `url`, if the optional qrcode package
    is installed; otherwise say how to get one."""
    try:
        import qrcode
    except ImportError:
        print("(Tip: `pip install qrcode` and restart to get a scannable")
        print(" QR code printed here.)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aloud.web",
        description=(
            "Start Aloud's browser-based GUI. Local-only by default; "
            "use --host 0.0.0.0 to let phones/tablets on your network connect."
        ),
    )
    parser.add_argument(
        "--port", type=int, default=0, help="Port to listen on (default: pick a free one)"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Address to listen on (default: 127.0.0.1, this machine only). "
            "Use 0.0.0.0 (or one specific interface address) to allow other "
            "devices on your local network - a link with an access token and "
            "a QR code to scan are printed at startup."
        ),
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Don't open the browser automatically"
    )
    args = parser.parse_args(argv)

    try:
        server = create_server(port=args.port, host=args.host)
    except OSError as exc:
        print(f"error: cannot start server: {exc}", file=sys.stderr)
        return 2

    port = server.server_address[1]
    if args.host in LOOPBACK_HOSTS:
        url = f"http://127.0.0.1:{port}/"
        print(f"Aloud is running at {url}", flush=True)
    else:
        # LAN mode: the page itself requires the token, so every URL we hand
        # out (and the QR code) must carry it.
        query = f"?t={server.RequestHandlerClass.token}"
        url = f"http://127.0.0.1:{port}/{query}"
        print(f"Aloud is running at {url}", flush=True)
        lan_ip = _lan_ip(args.host)
        if lan_ip:
            lan_url = f"http://{lan_ip}:{port}/{query}"
            print("On a phone or another device on this network, open:", flush=True)
            print(f"  {lan_url}", flush=True)
            _print_qr(lan_url)
        else:
            print(
                "Could not determine this machine's network address; on another "
                f"device, open http://<this machine's IP>:{port}/{query}",
                flush=True,
            )
        print(
            "Note: anyone on your network with this exact link can use Aloud "
            "while it runs.",
            flush=True,
        )
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
