from datetime import datetime, timezone
import unittest

from test_support import locate
locate()

from controlplane.evidence import CurrentSubject, evaluate_evidence
from controlplane.models import EvidenceBinding


class EvidenceTests(unittest.TestCase):
    def binding(self, **changes):
        value = dict(
            evidence_id="evidence:1", subject_commit="abc", lock_hash="lock",
            contract_hash="contract", artifact_digest="artifact",
            environment_hash="env", verdict="PASS",
            verified_at="2026-07-27T00:00:00+00:00",
            expires_at="2026-07-28T00:00:00+00:00", oracle="unit",
        )
        value.update(changes)
        return EvidenceBinding(**value)

    def current(self, **changes):
        value = dict(subject_commit="abc", lock_hash="lock", contract_hash="contract", artifact_digest="artifact", environment_hash="env")
        value.update(changes)
        return CurrentSubject(**value)

    def test_fresh_when_all_bindings_match(self):
        result = evaluate_evidence(self.binding(), self.current(), now=datetime(2026, 7, 27, 1, tzinfo=timezone.utc))
        self.assertEqual(result.state, "VERIFIED_FRESH")

    def test_commit_change_invalidates(self):
        result = evaluate_evidence(self.binding(), self.current(subject_commit="def"), now=datetime(2026, 7, 27, 1, tzinfo=timezone.utc))
        self.assertEqual(result.state, "STALE")
        self.assertIn("subject_commit_changed", result.reasons)

    def test_fake_clock_expiry(self):
        result = evaluate_evidence(self.binding(), self.current(), now=datetime(2026, 7, 29, tzinfo=timezone.utc))
        self.assertEqual(result.state, "STALE")
        self.assertIn("lease_expired", result.reasons)


if __name__ == "__main__":
    unittest.main()
