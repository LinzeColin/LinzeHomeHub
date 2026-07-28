import tempfile
from pathlib import Path
import unittest

from test_support import locate
locate()

from controlplane.db import IdempotencyConflict, RevisionConflict, RuntimeStore


class RuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RuntimeStore(Path(self.temp.name) / "status.db")
        self.store.migrate()

    def tearDown(self):
        self.temp.cleanup()

    def test_command_journal_fact_outbox_are_atomic_and_idempotent(self):
        first = self.store.apply_command(
            idempotency_key="prices:command:0001",
            command_type="replace_prices",
            expected_revision=0,
            actor_hash="actor-hash",
            payload={"items": [{"name": "OVH", "amount": 1}]},
            fact_type="status.prices",
            now="2026-07-27T00:00:00+00:00",
        )
        replay = self.store.apply_command(
            idempotency_key="prices:command:0001",
            command_type="replace_prices",
            expected_revision=0,
            actor_hash="actor-hash",
            payload={"items": [{"name": "OVH", "amount": 1}]},
            fact_type="status.prices",
            now="2026-07-27T00:01:00+00:00",
        )
        self.assertEqual(first.committed_revision, 1)
        self.assertTrue(replay.replayed)
        self.assertEqual(len(self.store.pending_outbox()), 1)
        self.assertEqual(self.store.current_revision(), 1)
        self.assertEqual(self.store.latest_fact("status.prices")["payload"]["items"][0]["name"], "OVH")

    def test_idempotency_key_reuse_with_different_request_is_rejected(self):
        self.store.apply_command(
            idempotency_key="prices:command:0001", command_type="replace",
            expected_revision=0, actor_hash="actor", payload={"items": []},
            fact_type="status.prices",
        )
        with self.assertRaises(IdempotencyConflict):
            self.store.apply_command(
                idempotency_key="prices:command:0001", command_type="replace",
                expected_revision=0, actor_hash="actor", payload={"items": [{"name": "changed"}]},
                fact_type="status.prices",
            )

    def test_failed_outbox_honours_retry_time_and_budget(self):
        outcome = self.store.apply_command(
            idempotency_key="prices:command:0001", command_type="replace",
            expected_revision=0, actor_hash="actor", payload={"items": []},
            fact_type="status.prices", now="2026-07-27T00:00:00+00:00",
        )
        self.store.mark_failed(outcome.event_id, "network", "2026-07-27T01:00:00+00:00")
        self.assertEqual(self.store.pending_outbox(now="2026-07-27T00:30:00+00:00"), [])
        self.assertEqual(len(self.store.pending_outbox(now="2026-07-27T01:00:00+00:00")), 1)
        for _ in range(4):
            self.store.mark_failed(outcome.event_id, "network")
        self.assertEqual(self.store.pending_outbox(now="2026-07-27T02:00:00+00:00", max_attempts=5), [])

    def test_stale_revision_rejected(self):
        self.store.apply_command(
            idempotency_key="prices:command:0001", command_type="replace",
            expected_revision=0, actor_hash="actor", payload={"items": []},
            fact_type="status.prices",
        )
        with self.assertRaises(RevisionConflict):
            self.store.apply_command(
                idempotency_key="prices:command:0002", command_type="replace",
                expected_revision=0, actor_hash="actor", payload={"items": []},
                fact_type="status.prices",
            )

    def test_sent_event_leaves_pending_queue(self):
        outcome = self.store.apply_command(
            idempotency_key="prices:command:0001", command_type="replace",
            expected_revision=0, actor_hash="actor", payload={"items": []},
            fact_type="status.prices",
        )
        self.store.mark_sent(outcome.event_id, "2026-07-27T00:02:00+00:00")
        self.assertEqual(self.store.pending_outbox(), [])


if __name__ == "__main__":
    unittest.main()
