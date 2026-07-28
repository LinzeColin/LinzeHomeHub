from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from test_support import locate
locate()
from controlplane.collector import collect_control_plane

class CollectorTests(unittest.TestCase):
    def test_current_snapshot_shapes_and_empty_source_do_not_become_healthy(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); status=root/'snapshot.json'; github=root/'github_public.json'
            status.write_text(json.dumps({
                "updated_epoch":1785110400,
                "projects":[{"name":"Alpha","repo":"alpha","status":"run"}],
                "software":{"at":1785110400,"stages":[{"k":"run","n":"运行"}],"lines":[{"name":"Alpha","repo":"alpha","state":"ok","score":100,"judged":1,"stages_total":1,"cells":{"run":{"s":"ok","v":"running"}}}],"units":[{"kind":"container","id":"alpha-web","state":"running"}]},
                "graph":{"nodes":[{"id":"p:alpha","label":"Alpha","kind":"project","status":"run"}],"edges":[]}
            }),encoding='utf-8')
            github.write_text(json.dumps({"available":False,"collected_epoch":1785110400,"public_repos":[]}),encoding='utf-8')
            out_private=root/'private.json'; out_public=root/'public.json'
            result=collect_control_plane(status_path=status,github_path=github,output_private=out_private,output_public=out_public,now=datetime.fromtimestamp(1785110400,tz=timezone.utc))
            self.assertEqual(result["portfolio"]["coverage_health"],"UNKNOWN")
            self.assertEqual(len(result["business_lines"]),1)
            self.assertEqual(len(result["capabilities"]),1)
            self.assertEqual(len(result["architecture"]["nodes"]),1)
            public=json.loads(out_public.read_text())
            self.assertFalse(public["portfolio"]["unknown_is_healthy"])

    def test_stale_snapshot_never_reports_healthy_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); status=root/'snapshot.json'; github=root/'github_public.json'
            status.write_text(json.dumps({"updated_epoch":1,"projects":[{"name":"Alpha","repo":"alpha","status":"run"}]}))
            github.write_text(json.dumps({"collected_epoch":1,"public_repos":[{"name":"alpha"}]}))
            result=collect_control_plane(status_path=status,github_path=github,output_private=root/'pvt.json',output_public=root/'pub.json',now=datetime(2026,7,27,tzinfo=timezone.utc))
            self.assertEqual(result["portfolio"]["coverage_health"],"UNKNOWN")
            self.assertEqual(result["portfolio"]["status_snapshot_freshness"],"STALE")

if __name__ == "__main__": unittest.main()
