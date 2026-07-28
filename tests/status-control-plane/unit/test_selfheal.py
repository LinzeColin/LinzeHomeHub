from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from test_support import locate
locate()

from controlplane.commands import CommandResult
from controlplane.selfheal import TargetPolicy, heal_target


class SequenceRunner:
    def __init__(self, values): self.values=list(values)
    def __call__(self, *_args, **_kwargs): return self.values.pop(0)


def result(ok):
    return CommandResult(("test",), 0 if ok else 1, "", "")


class SelfHealTests(unittest.TestCase):
    def policy(self):
        return TargetPolicy("status", ("curl", "probe"), ("docker", "restart", "linze-status"), cooldown_seconds=0)

    def test_false_success_is_impossible(self):
        with tempfile.TemporaryDirectory() as td:
            outcome = heal_target(self.policy(), state_path=Path(td)/"state.json", lock_path=Path(td)/"lock", now=datetime(2026,7,27,tzinfo=timezone.utc), runner=SequenceRunner([result(False), result(False)]))
            self.assertEqual(outcome.state, "ACTION_FAILED")

    def test_recovered_requires_post_probe(self):
        with tempfile.TemporaryDirectory() as td:
            outcome = heal_target(self.policy(), state_path=Path(td)/"state.json", lock_path=Path(td)/"lock", now=datetime(2026,7,27,tzinfo=timezone.utc), runner=SequenceRunner([result(False), result(True), result(True)]))
            self.assertEqual(outcome.state, "RECOVERED")

    def test_post_probe_failure_is_failed(self):
        with tempfile.TemporaryDirectory() as td:
            outcome = heal_target(self.policy(), state_path=Path(td)/"state.json", lock_path=Path(td)/"lock", now=datetime(2026,7,27,tzinfo=timezone.utc), runner=SequenceRunner([result(False), result(True), result(False)]))
            self.assertEqual(outcome.state, "FAILED")


if __name__ == "__main__":
    unittest.main()
