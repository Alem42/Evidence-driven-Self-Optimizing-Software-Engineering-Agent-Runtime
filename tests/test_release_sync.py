"""Exercise deployment failure and rollback without changing Docker or the host."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipIf(os.name == 'nt', 'Linux updater uses flock; exercised inside the container')
class ReleaseSyncTests(unittest.TestCase):
    def setUp(self):
        source = Path(os.environ.get('MASA_SYNC_SCRIPT') or str(Path(__file__).resolve().parent.parent / 'deploy/sync_release.py'))
        spec = importlib.util.spec_from_file_location('release_sync_under_test', source)
        self.sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.sync)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sync.ROOT = self.root
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        for name in self.sync.FILES:
            (self.bundle / name).write_text('synthetic ' + name)
        self.manifest = {'schema': 1, 'tag': 'v1.0.0', 'revision': 'a' * 40, 'image_id': 'sha256:' + 'b' * 64,
                         'image_tag': 'masa-release:' + 'a' * 40, 'image_revision': 'a' * 40,
                         'sha256': {name: hashlib.sha256((self.bundle / name).read_bytes()).hexdigest() for name in self.sync.FILES}}
        self.previous = {'tag': 'v0.9.0', 'image_id': 'sha256:' + 'c' * 64, 'directory': str(self.root / 'previous'), 'revision': 'd' * 40}
        self.sync.atomic_json(self.root / 'current.json', self.previous)

    def docker(self, args, **kwargs):
        stdout = ''
        if args[:3] == ['docker', 'image', 'inspect']:
            stdout = json.dumps([{'Id': 'sha256:' + 'e' * 64, 'Config': {'Labels': {'org.opencontainers.image.revision': 'a' * 40}}}])
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    def test_corrupted_asset_rejected_before_docker(self):
        (self.bundle / 'masa-image.tar.gz').write_bytes(b'corrupt')
        with patch.object(self.sync, 'command') as command, self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.sync.install(self.bundle, self.manifest)
        command.assert_not_called()

    def test_active_work_leaves_current_container_and_image_unchanged(self):
        with patch.object(self.sync, 'probe', side_effect=subprocess.CalledProcessError(1, ['docker', 'exec'])) as probe, \
                patch.object(self.sync, 'command') as command, self.assertRaises(subprocess.CalledProcessError):
            self.sync.install(self.bundle, self.manifest)
        command.assert_not_called()
        probe.assert_called_once_with(self.previous, idle=True)
        self.assertEqual(self.sync.read_json(self.root / 'current.json'), self.previous)

    def test_unhealthy_update_restores_previous_and_does_not_change_checkpoint(self):
        with patch.object(self.sync, 'command', side_effect=self.docker), patch.object(self.sync, 'compose') as compose, \
                patch.object(self.sync, 'probe') as probe, \
                patch.object(self.sync, 'health', side_effect=[RuntimeError('unhealthy'), None]), \
                self.assertRaisesRegex(RuntimeError, 'unhealthy'):
            self.sync.install(self.bundle, self.manifest)
        self.assertEqual(compose.call_args_list[-1].args[0], self.previous)
        self.assertEqual(probe.call_args_list[-1].args[0], self.previous)
        self.assertEqual(self.sync.read_json(self.root / 'current.json'), self.previous)
        self.assertFalse((self.root / 'previous.json').exists())

    def test_success_records_rollback_only_after_probe_passes(self):
        with patch.object(self.sync, 'command', side_effect=self.docker), \
                patch.object(self.sync, 'compose'), patch.object(self.sync, 'probe'), patch.object(self.sync, 'health'):
            self.sync.install(self.bundle, self.manifest)
        self.assertEqual(self.sync.read_json(self.root / 'previous.json'), self.previous)
        current = self.sync.read_json(self.root / 'current.json')
        self.assertEqual(current['image_id'], 'sha256:' + 'e' * 64)
        self.assertEqual(current['source_image_id'], self.manifest['image_id'])


if __name__ == '__main__':
    unittest.main()
