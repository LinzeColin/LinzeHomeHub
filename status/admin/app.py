#!/usr/bin/env python3
"""Cloudflare Access protected, revision-safe status administration service."""

from __future__ import annotations

from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

import jwt
from jwt import PyJWKClient

APP_ROOT = Path(__file__).resolve().parent
CONTROLPLANE = APP_ROOT / "controlplane"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from controlplane.db import IdempotencyConflict, RevisionConflict, RuntimeStore, StoreError
from controlplane.prices import validate_prices

TEAM = os.environ["CF_TEAM_DOMAIN"].strip().removeprefix("https://").rstrip("/")
AUDIENCE = os.environ["CF_ACCESS_AUD"].strip()
OWNER = os.environ["OWNER_EMAIL"].strip().lower()
ISSUER = os.environ.get("CF_ACCESS_ISSUER", f"https://{TEAM}").rstrip("/")
DB_PATH = Path(os.environ.get("RUNTIME_DB_PATH", "/srv/runtime/status.db"))
LEGACY_PRICES = Path(os.environ.get("PRICES_PATH", "/srv/data/prices.json"))
GITHUB_PRIVATE = Path(os.environ.get("GITHUB_PRIVATE", "/srv/private/github.json"))
PORT = int(os.environ.get("PORT", "8080"))
STATIC = APP_ROOT / "static"
MAX_BODY = 100_000
IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
_jwks = PyJWKClient(f"https://{TEAM}/cdn-cgi/access/certs", cache_jwk_set=True, lifespan=300)
_store = RuntimeStore(DB_PATH)
_store.migrate()


def _actor_hash(email: str) -> str:
    return sha256(email.encode("utf-8")).hexdigest()


def verify_identity(headers) -> str | None:
    token = headers.get("Cf-Access-Jwt-Assertion")
    if not token:
        return None
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "aud", "iss"]},
        )
    except Exception:
        return None
    email = str(claims.get("email") or "").strip().lower()
    return email if email == OWNER else None


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default



def current_prices() -> dict:
    fact = _store.latest_fact("status.prices")
    if fact:
        return {"revision": _store.current_revision(), "fact_revision": fact["revision"], **fact["payload"]}
    legacy = _load_json(LEGACY_PRICES, {"items": []})
    try:
        clean = validate_prices(legacy)
    except ValueError:
        clean = {"schema_version": 1, "items": []}
    return {"revision": _store.current_revision(), **clean}


class Handler(BaseHTTPRequestHandler):
    server_version = "linze-status-admin/0.0.0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_args):
        return

    def _headers(self, content_type: str, content_length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'",
        )

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self._headers(content_type, len(body))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, value) -> None:
        self._send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _static(self, name: str, content_type: str) -> None:
        allowed = {"index.html", "github.html", "admin.css", "prices.js", "github.js"}
        if name not in allowed:
            return self._json(404, {"error": "资源不存在"})
        try:
            body = (STATIC / name).read_bytes()
        except OSError:
            return self._json(404, {"error": "资源不可用"})
        self._send(200, body, content_type)

    def _identity(self) -> str | None:
        return verify_identity(self.headers)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/admin/healthz":
            return self._json(200, {"ok": True, "revision": _store.current_revision()})
        if not self._identity():
            return self._json(403, {"error": "未通过 Cloudflare Access 校验"})
        routes = {
            "/admin": ("index.html", "text/html; charset=utf-8"),
            "/admin/github": ("github.html", "text/html; charset=utf-8"),
            "/admin/assets/admin.css": ("admin.css", "text/css; charset=utf-8"),
            "/admin/assets/prices.js": ("prices.js", "text/javascript; charset=utf-8"),
            "/admin/assets/github.js": ("github.js", "text/javascript; charset=utf-8"),
        }
        if path in routes:
            return self._static(*routes[path])
        if path == "/admin/api/prices":
            return self._json(200, current_prices())
        if path == "/admin/api/github":
            return self._json(200, _load_json(GITHUB_PRIVATE, {"available": False, "note": "GitHub 私有采集不可用"}))
        return self._json(404, {"error": "路径不存在"})

    def do_POST(self):
        identity = self._identity()
        if not identity:
            return self._json(403, {"error": "未通过 Cloudflare Access 校验"})
        path = urlparse(self.path).path.rstrip("/")
        if path not in {"/admin/api/prices", "/admin/api/toggle"}:
            return self._json(404, {"error": "路径不存在"})
        if self.headers.get_content_type() != "application/json":
            return self._json(415, {"error": "Content-Type 必须是 application/json"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json(400, {"error": "Content-Length 非法"})
        if size <= 0 or size > MAX_BODY:
            return self._json(413, {"error": "请求体大小非法"})
        key = str(self.headers.get("Idempotency-Key", ""))
        if not IDEMPOTENCY.fullmatch(key):
            return self._json(400, {"error": "缺少或非法的 Idempotency-Key"})
        match = str(self.headers.get("If-Match", "")).strip().strip('"')
        try:
            expected_revision = int(match)
        except ValueError:
            return self._json(428, {"error": "If-Match 必须是当前 revision"})
        try:
            payload = json.loads(self.rfile.read(size))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "JSON 解析失败"})

        try:
            if path == "/admin/api/toggle":
                if not isinstance(payload, dict) or set(payload) != {"name", "auto_renew"}:
                    raise ValueError("toggle 请求只能包含 name 和 auto_renew")
                if not isinstance(payload.get("auto_renew"), bool):
                    raise ValueError("auto_renew 必须是布尔值")
                current = current_prices()
                name = str(payload.get("name", "")).strip()
                changed = False
                items = []
                for item in current.get("items", []):
                    row = dict(item)
                    if row.get("name") == name:
                        row["auto_renew"] = payload["auto_renew"]
                        changed = True
                    items.append(row)
                if not changed:
                    return self._json(404, {"error": "未找到开支项"})
                clean = validate_prices({"items": items})
                command_type = "toggle_auto_renew"
            else:
                clean = validate_prices(payload)
                command_type = "replace_prices"
            outcome = _store.apply_command(
                idempotency_key=key,
                command_type=command_type,
                expected_revision=expected_revision,
                actor_hash=_actor_hash(identity),
                payload=clean,
                fact_type="status.prices",
            )
        except IdempotencyConflict:
            return self._json(409, {"error": "幂等键已被不同请求占用"})
        except RevisionConflict:
            return self._json(409, {"error": "版本冲突", "current_revision": _store.current_revision()})
        except (ValueError, StoreError) as exc:
            return self._json(400, {"error": str(exc)})
        return self._json(200, {"ok": True, "revision": outcome.committed_revision, "replayed": outcome.replayed, **clean})


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
