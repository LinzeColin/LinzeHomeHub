"""A shared tunnel must preserve the host that each forwarding edge serves."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import arch


class HostScopedRoutes(unittest.TestCase):
    def test_shared_tunnel_keeps_each_host_bound_to_its_service(self):
        units = [
            {"name": f"{key}.service", "state": "active/running", "role": "service",
             "kind": "service", "desc": key}
            for key in ("alpha", "beta")
        ]
        tunnels = [{"name": "shared", "id": "fixture", "connections": 1, "ingress": [
            {"host": "one.example", "service": "http://127.0.0.1:18101"},
            {"host": "two.example", "service": "http://localhost:18102"},
            {"host": "broken.example", "service": "http://[::1]:not-a-port"},
        ]}]
        with patch.multiple(arch, docker_units=lambda: [], systemd_units=lambda: units,
                            listeners=lambda: {18101: "alpha.service", 18102: "beta.service"},
                            cf_tunnels=lambda: (tunnels, ""), file_routes=lambda: [],
                            owners_from_snapshot=lambda: {}, sh=lambda *args: ""):
            result = arch.build()
        edges = result["edges"]
        forwarding = {e["to"]: e["hosts"] for e in edges if e["kind"] == "转发"}
        self.assertEqual(forwarding, {"u:alpha.service": ["one.example"], "u:beta.service": ["two.example"]})
        self.assertTrue(any(e["from"] == "d:broken.example" and e["kind"] == "入口" for e in edges))
        self.assertEqual(result["counts"]["域名"], 3)


if __name__ == "__main__":
    unittest.main()
