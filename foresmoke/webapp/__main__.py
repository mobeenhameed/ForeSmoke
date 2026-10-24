"""Launch the ForeSmoke review webapp: python -m foresmoke.webapp"""
from __future__ import annotations

import argparse
import logging
import threading
import webbrowser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args(argv)
    try:
        import uvicorn
    except ImportError:
        print("Install the webapp extra first: python -m pip install -e '.[perception,reasoning,webapp]'")
        return 2
    from .server import create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
    if args.host not in ("127.0.0.1", "localhost"):
        logging.warning("Binding to %s exposes the review tool without authentication.", args.host)
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"ForeSmoke review running at {url}  (Ctrl+C to stop)", flush=True)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
