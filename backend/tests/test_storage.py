"""Regression coverage for cloud authority, integrity and safe reprocessing."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import storage, supabase_storage as remote


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='chart-storage-unit-')
        self.root = Path(self.temp.name)
        self.settings = SimpleNamespace(storage_backend='local', storage_path=self.root,
                                        supabase_bucket='test', supabase_table='documents')
        self.config = patch.object(storage, 'get_settings', return_value=self.settings)
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.temp.cleanup()

    def test_local_mode_keeps_originals_versions_and_history(self):
        directory = storage.doc_dir('local-doc')
        original = directory / 'original.pdf'
        original.write_bytes(b'original bytes')
        m = storage.create_manifest('local-doc', 'Local', 'original.pdf', [{'kind': 'pdf', 'filename': 'original.pdf'}])
        m['charts'] = [{'chart_id': 'c1', 'versions': [{'spec': {'chart_type': 'line'}}], 'current_version': 0, 'final_version': None}]
        storage.write_manifest(m)
        storage.add_version('local-doc', 'c1', {'chart_type': 'bar'}, 'Edit', 'edit')
        self.assertEqual(storage.read_manifest('local-doc')['charts'][0]['current_version'], 1)
        self.assertEqual(storage.read_index()[0]['chart_types'], {'bar': 1})
        self.assertEqual(storage.source_paths('local-doc', m)[0][1].read_bytes(), b'original bytes')

    def test_cloud_deletion_cannot_resurrect_a_local_manifest(self):
        storage.create_manifest('old', 'Old', 'old.pdf')
        self.settings.storage_backend = 'supabase'
        with patch.object(remote, 'get_record', return_value=None):
            self.assertIsNone(storage.read_manifest('old'))

    def test_cloud_download_restores_and_repairs_corrupt_cache(self):
        self.settings.storage_backend = 'supabase'
        data = b'preserve exact original bytes'
        asset = {'key': 'cloud/original.pdf', 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        row = {'manifest': {'document_id': 'cloud'}, 'revision': 1, 'assets': {'original.pdf': asset}}
        with patch.object(remote, 'get_record', return_value=row), patch.object(remote, 'download_asset', return_value=data) as download:
            target = storage.asset_path('cloud', 'original.pdf')
            self.assertEqual(target.read_bytes(), data)
            target.write_bytes(b'corrupted cache')
            self.assertEqual(storage.asset_path('cloud', 'original.pdf').read_bytes(), data)
            self.assertEqual(download.call_count, 2)

    def test_failed_cloud_save_does_not_claim_local_success(self):
        self.settings.storage_backend = 'supabase'
        with patch.object(remote, 'save_record', side_effect=remote.StorageError('offline')):
            with self.assertRaises(remote.StorageError):
                storage.create_manifest('unsaved', 'Unsaved', 'original.pdf')
        self.assertFalse((self.root / 'unsaved/manifest.json').exists())

    def test_invalid_paths_are_rejected_before_file_access_or_delete(self):
        for document_id in ('..', '../outside', 'a/b', ''):
            with self.assertRaises(ValueError):
                storage.delete_document(document_id)
        with self.assertRaises(ValueError):
            storage.asset_path('safe', '../secret.env')

    def test_asset_hash_mismatch_is_not_accepted(self):
        with patch.object(remote, 'get_settings', return_value=self.settings), patch.object(remote, 'request', return_value=b'corrupt'):
            with self.assertRaises(remote.StorageError):
                remote.download_asset({'key': 'file.pdf', 'sha256': hashlib.sha256(b'original').hexdigest()})

    def test_stale_remote_revision_cannot_overwrite_newer_document(self):
        manifest = {'document_id': 'doc', 'created_at': '2026-01-01', 'updated_at': '2026-01-01', '_storage_revision': 1}
        with patch.object(remote, 'get_settings', return_value=self.settings), \
             patch.object(remote, 'request', return_value=[]), \
             patch.object(remote, 'get_record', return_value={'revision': 3, 'manifest': {}, 'assets': {}}):
            with self.assertRaises(remote.StorageConflict):
                remote.save_record(manifest, {}, self.root)

    def test_summary_does_not_count_old_final_version_or_double_count_reruns(self):
        m = storage.create_manifest('summary', 'Summary', 'file.pdf')
        m['charts'] = [{'chart_id': 'c', 'current_version': 1, 'final_version': 0,
                        'versions': [{'spec': {'chart_type': 'line'}}, {'spec': {'chart_type': 'line'}}]}]
        m['usage'] = {'totals': {'cost_usd': 1.0}, 'pricing': {'known': True},
                      'reruns': [{'cost_usd': 0.5}], 'gate': {'totals': {'cost_usd': 0.1}, 'pricing': {'known': True}}}
        summary = storage.document_summary(m)
        self.assertEqual(summary['finalized_count'], 0)
        self.assertAlmostEqual(summary['estimated_cost_usd'], 1.1)


if __name__ == '__main__':
    unittest.main()
