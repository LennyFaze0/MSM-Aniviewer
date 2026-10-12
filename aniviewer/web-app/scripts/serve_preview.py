#!/usr/bin/env python3
"""Serve the production web build under its GitHub Pages base path."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


BASE_PATH = "/MSM-Aniviewer"


class PreviewHandler(SimpleHTTPRequestHandler):
    def _strip_base_path(self) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        if path == BASE_PATH:
            path = "/"
        elif path.startswith(f"{BASE_PATH}/"):
            path = path[len(BASE_PATH) :]
        self.path = urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        self._strip_base_path()
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        self._strip_base_path()
        super().do_HEAD()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()

    dist_dir = Path(__file__).resolve().parents[1] / "dist"
    if not (dist_dir / "index.html").is_file():
        raise SystemExit("The production build is missing. Run `npm run build` first.")

    handler = partial(PreviewHandler, directory=str(dist_dir))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving {dist_dir} at http://{args.host}:{args.port}{BASE_PATH}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
