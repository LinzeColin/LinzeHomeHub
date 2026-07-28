from pathlib import Path
import tempfile, unittest
from status.controlplane.backup_transport import LocalMirrorTransport, backup_replicate_restore, build_manifest, verify_restore
class BackupTest(unittest.TestCase):
    def test_r2_and_oci_independent_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source'; source.mkdir();
            (source/'facts.json').write_text('{"records":2,"critical":"present"}\n',encoding='utf-8')
            (source/'nested').mkdir(); (source/'nested/blob.bin').write_bytes(b'abc')
            contract={'required_paths':['facts.json'],'json_assertions':[{'path':'facts.json','pointer':'/records','expected':2},{'path':'facts.json','pointer':'/critical','expected':'present'}]}
            result=backup_replicate_restore(source,r2_prefix='r2crypt:backups/private-database',oci_prefix='ocicrypt:r2-d1-cold-backup',transport=LocalMirrorTransport(root/'remote'),evidence_path=root/'evidence.json',semantic_contract=contract)
            self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED')
            self.assertEqual(result['r2']['verification']['actual_count'],2)
            self.assertEqual(result['oci']['verification']['actual_count'],2)
            self.assertEqual(result['r2']['semantic']['state'],'SEMANTIC_VERIFIED')
            self.assertEqual(result['oci']['semantic']['state'],'SEMANTIC_VERIFIED')
    def test_extra_file_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source'; source.mkdir(); (source/'a').write_text('a')
            manifest=build_manifest(source); restored=root/'restored'; shutil_source=source/'a'; restored.mkdir(); (restored/'a').write_text('a'); (restored/'extra').write_text('x')
            self.assertEqual(verify_restore(manifest,restored)['state'],'RESTORE_FAILED')
if __name__ == '__main__': unittest.main()
