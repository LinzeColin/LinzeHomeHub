from pathlib import Path
import tempfile
import unittest

from test_support import locate
locate()

from controlplane.backup import build_manifest, verify_restore


class BackupTests(unittest.TestCase):
    def test_restore_requires_digest_match(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"source"; restored=Path(td)/"restored"; root.mkdir(); restored.mkdir()
            (root/"fact.json").write_text('{"ok":true}',encoding='utf-8')
            (restored/"fact.json").write_text('{"ok":true}',encoding='utf-8')
            manifest=build_manifest(root,[root/"fact.json"],encryption_profile='fixture')
            self.assertEqual(verify_restore(manifest,restored)["state"],"RESTORE_VERIFIED")
            (restored/"fact.json").write_text('{"ok":false}',encoding='utf-8')
            self.assertEqual(verify_restore(manifest,restored)["state"],"RESTORE_FAILED")


if __name__ == "__main__":
    unittest.main()
