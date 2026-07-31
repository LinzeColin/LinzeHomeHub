"""Cloudflare Tunnel 的 HTTP origin 路由不得重定向回自身。"""

from __future__ import annotations

import unittest

from test_support import locate

REPO, _, _ = locate()
COMPOSE = REPO / "status" / "deploy" / "docker-compose.yml"


class StatusTunnelOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = COMPOSE.read_text(encoding="utf-8")

    def test_http_tunnel_origin_targets_status_service(self):
        self.assertIn(
            "traefik.http.routers.linzestatus-http.service=linzestatus",
            self.compose,
        )

    def test_http_tunnel_origin_has_no_https_redirect_middleware(self):
        self.assertNotIn(
            "traefik.http.routers.linzestatus-http.middlewares=",
            self.compose,
        )
        self.assertNotIn(
            "traefik.http.middlewares.linzestatus-redir.redirectscheme.scheme=",
            self.compose,
        )

    def test_public_https_router_remains_enabled(self):
        self.assertIn("traefik.http.routers.linzestatus.entrypoints=https", self.compose)
        self.assertIn("traefik.http.routers.linzestatus.tls=true", self.compose)


if __name__ == "__main__":
    unittest.main()
