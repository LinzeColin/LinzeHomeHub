import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_support import locate
locate()

from controlplane.authority import sync_events


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "private"
        self.repo.mkdir()
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@seed.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("authority\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.repo, check=True, capture_output=True)
        self.event = {"event_id": "event:1", "fact_type": "status.release", "completed_at": "2026-07-27T00:00:00+00:00", "payload": {"release": "v1"}}

    def tearDown(self):
        self.temp.cleanup()

    def test_dirty_authority_worktree_is_rejected(self):
        (self.repo / "dirty.txt").write_text("dirty")
        from controlplane.authority import AuthoritySyncError
        with self.assertRaises(AuthoritySyncError):
            sync_events(self.repo, [self.event], commit_message="fact")

    def test_identical_fact_creates_no_second_commit(self):
        first = sync_events(self.repo, [self.event], commit_message="fact")
        head1 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        second = sync_events(self.repo, [self.event], commit_message="same fact")
        head2 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        self.assertTrue(first["committed"])
        self.assertEqual(second["state"], "NO_NEW_FACT")
        self.assertEqual(head1, head2)


if __name__ == "__main__":
    unittest.main()
