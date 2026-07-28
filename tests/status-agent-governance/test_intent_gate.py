import json, unittest
from pathlib import Path
from status.controlplane.intent import compile_run_intent, IntentError, verify_bundle
from status.controlplane.gate import evaluate
class IntentGateTest(unittest.TestCase):
    def test_intent_is_deterministic_and_rejects_secret_keys(self):
        owner={'principles':['verification-first'],'hard_constraints':['0 runtime model']}
        project={'project_id':'status.linzezhang.com','target_repository':'LinzeColin/LinzeHomeHub','target_area':'status/'}
        task={'task_id':'T-1','goal':'verify status','acceptance_criteria':['evidence bound'],'allowed_paths':['status/'],'forbidden_paths':['src/']}
        a=compile_run_intent(owner,project,task,created_at='2026-07-28T00:00:00+00:00')
        b=compile_run_intent(owner,project,task,created_at='2026-07-28T00:00:00+00:00')
        self.assertEqual(a,b); self.assertTrue(verify_bundle(a))
        with self.assertRaises(IntentError): compile_run_intent({'api_key':'x'},project,task)
    def test_gate_fails_closed(self):
        root=Path(__file__).parent/'fixtures'
        contract=json.loads((root/'gate_contract.json').read_text())
        passed=json.loads((root/'gate_observations_pass.json').read_text())
        unknown=json.loads((root/'gate_observations_unknown.json').read_text())
        self.assertEqual(evaluate(contract,passed,verified_at='2026-07-28T00:00:00+00:00')['verdict'],'PASS')
        self.assertEqual(evaluate(contract,unknown,verified_at='2026-07-28T00:00:00+00:00')['verdict'],'BLOCKED')
        mismatched=dict(passed); mismatched['subject_commit']='d'*40
        self.assertEqual(evaluate(contract,mismatched)['reason'],'SUBJECT_BINDING_MISMATCH')
if __name__ == '__main__': unittest.main()
