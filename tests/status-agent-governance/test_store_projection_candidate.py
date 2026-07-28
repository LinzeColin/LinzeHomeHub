from datetime import datetime, timezone
from pathlib import Path
import tempfile, unittest
from status.controlplane.agent_store import AgentStore
from status.controlplane.agent_projection import build_projection
from status.controlplane.candidate import build_candidate
class StoreProjectionTest(unittest.TestCase):
    def test_store_projection_and_owner_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            store=AgentStore(Path(directory)/'status.db'); store.migrate()
            store.upsert_run({'run_id':'run-1','project_id':'status.linzezhang.com','task_id':'task-1','provider':'codex','intent_hash':'h'*64,'status':'RUNNING','started_at':'2026-07-28T00:00:00+00:00'})
            store.add_event({'event_id':'event-1','run_id':'run-1','session_id':'session-1','provider':'codex','event_type':'PostToolUse','occurred_at':'2026-07-28T00:00:00+00:00','safe_payload':{'exit_code':0},'redaction_count':0,'adapter_state':'NORMALIZED_REDACTED'})
            candidate=build_candidate(run_id='run-1',title='repeatable verification',signals={},evidence_refs=['evidence-1'],created_at='2026-07-28T00:00:00+00:00')
            store.add_candidate(candidate)
            snap=store.snapshot(); self.assertEqual(len(snap['runs']),1)
            projection=build_projection(snap,now=datetime(2026,7,28,tzinfo=timezone.utc),ttl_minutes=30)
            self.assertEqual(projection['release_decision']['state'],'UNKNOWN')
            self.assertTrue(candidate['requires_owner_approval'])
            self.assertEqual(candidate['state'],'PROPOSED')
if __name__ == '__main__': unittest.main()
