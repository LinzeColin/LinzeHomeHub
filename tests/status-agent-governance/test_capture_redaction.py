import json, unittest
from pathlib import Path
from status.controlplane.capture import normalize_event
class CaptureTest(unittest.TestCase):
    def test_codex_and_claude_are_redacted(self):
        root=Path(__file__).parent/'fixtures'
        for provider,name in [('codex','codex_event.json'),('claude','claude_event.json')]:
            raw=json.loads((root/name).read_text(encoding='utf-8'))
            event=normalize_event(raw,provider=provider,project_id='status.linzezhang.com',run_id='run-1',task_id='task-1',intent_hash='h'*64,session_id='session-1')
            serialized=json.dumps(event,ensure_ascii=False)
            self.assertNotIn('secret-value-for-redaction',serialized)
            self.assertEqual(event['adapter_state'],'NORMALIZED_REDACTED')
            self.assertGreater(event['redaction_count'],0)
            self.assertIn('content_sha256',serialized)
if __name__ == '__main__': unittest.main()
