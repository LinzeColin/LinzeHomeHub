import unittest
from test_support import locate
locate()
from controlplane.projection import ProjectionError, build_public_projection, safe_public_url

class ProjectionTests(unittest.TestCase):
    def base(self):
        return {
            "schema_version":1,"generated_at":"2026-07-27T00:00:00+00:00","observed_revision":"x",
            "portfolio":{"coverage_health":"UNKNOWN","runtime_health":"UNKNOWN","project_count":0,"business_line_count":0,"unknown_is_healthy":False,"status_snapshot_freshness":"UNKNOWN","github_snapshot_freshness":"UNKNOWN"},
            "business_lines":[],"projects":[],"capabilities":[],
            "architecture":{"nodes":[],"edges":[],"provenance_mode":"DERIVED"},"conditions":[],
            "evidence_summary":{"verified_fresh":0,"stale":0,"unverified":0},
            "provenance_summary":{"native":0,"reconstructed":0,"unknown":0},
        }
    def test_public_projection_rejects_unknown_nested_field(self):
        value=self.base(); value["projects"]=[{"entity_id":"p","name":"P","private_repo_name":"secret"}]
        with self.assertRaises(ProjectionError): build_public_projection(value)
    def test_only_https_allowlisted_hosts(self):
        self.assertEqual(safe_public_url("https://github.com/LinzeColin"),"https://github.com/LinzeColin")
        self.assertIsNone(safe_public_url("javascript:alert(1)"))
        self.assertIsNone(safe_public_url("https://evil.invalid/path"))

if __name__ == "__main__": unittest.main()
