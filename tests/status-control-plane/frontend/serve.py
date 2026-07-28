#!/usr/bin/env python3
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse


class Server(ThreadingHTTPServer):
    web_root: Path
    data_root: Path


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/":
            path = "/index.html"
        root = self.server.data_root if path.startswith("/data/") else self.server.web_root
        relative = path.removeprefix("/data/") if path.startswith("/data/") else path.lstrip("/")
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return self.send_error(403)
        if not target.is_file():
            return self.send_error(404)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def find_repo(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "status" / "web").is_dir():
            return candidate
    raise SystemExit("无法定位包含 status/web 的目标仓库；请传入 --repo")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    repo = Path(args.repo).resolve() if args.repo else find_repo(Path.cwd().resolve())
    server = Server(("127.0.0.1", args.port), Handler)
    server.web_root = repo / "status" / "web"
    server.data_root = repo / "status" / "data"
    server.serve_forever()


if __name__ == "__main__":
    main()
