from pathlib import Path
import subprocess, tempfile, unittest
from status.controlplane.authority import sync_events, validate_client_contract

class FakeClient:
    def __init__(self): self.files = {}
    def __call__(self, command, **kwargs):
        args = command[2:]
        if args == ['--help']:
            return subprocess.CompletedProcess(command, 0, 'commands: put get ingest list verify', '')
        if args[0] == 'put':
            self.files[(args[1], args[2])] = Path(args[3]).read_bytes()
            return subprocess.CompletedProcess(command, 0, 'ok', '')
        if args[0] == 'get':
            Path(args[3]).write_bytes(self.files[(args[1], args[2])])
            return subprocess.CompletedProcess(command, 0, 'ok', '')
        return subprocess.CompletedProcess(command, 2, '', 'unsupported')

class AuthorityTest(unittest.TestCase):
    def test_no_clone_idempotent_readback(self):
        fake = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            client = Path(directory)/'private_db_client.py'; client.write_text('print()', encoding='utf-8')
            self.assertEqual(validate_client_contract(client, runner=fake)['state'], 'CLIENT_CONTRACT_VERIFIED')
            event={'event_id':'evt-1','fact_type':'status.release','completed_at':'2026-07-28T00:00:00+00:00','value':1}
            result=sync_events(client,[event],runner=fake)
            self.assertEqual(result['state'],'SYNCED')
            self.assertEqual(result['sent_event_ids'],['evt-1'])
            self.assertFalse(result['failed_event_ids'])
            self.assertEqual(sync_events(client,[],runner=fake)['state'],'NO_NEW_FACT')
    def test_implementation_contains_no_git_transport(self):
        source=(Path(__file__).parents[2]/'status/controlplane/authority.py').read_text(encoding='utf-8').lower()
        for phrase in ('git clone','git push','git commit','git add'):
            self.assertNotIn(phrase, source)
if __name__ == '__main__': unittest.main()
