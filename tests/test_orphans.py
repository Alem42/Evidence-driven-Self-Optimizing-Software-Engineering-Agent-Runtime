"""孤儿模型进程的检测与清理：只处理“确认父进程已不在的 Ollama llama-server”。
Orphaned model-runner detection and cleanup: only provably orphaned Ollama runners are ever touched."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.infrastructure import orphans

OLLAMA = str(Path(os.environ.get('LOCALAPPDATA', 'C:\\Users\\x\\AppData\\Local')) / 'Programs' / 'Ollama' / 'lib' / 'ollama' / 'llama-server.exe')


def listing(*items):
    return json.dumps(list(items))


def runner(pid, parent, alive, path=OLLAMA, mb=1000):
    return {'pid': pid, 'parent_pid': parent, 'parent_alive': alive, 'path': path, 'ws_mb': mb, 'started': '2026-10-04T01:00:00.0000000+08:00',
            'cmd': f'{path} --model C:\\Users\\x\\.ollama\\models\\blobs\\sha256-abcdef0123456789abcdef --port 1234'}


@unittest.skipUnless(os.name == 'nt', 'Windows only')
class OrphanTests(unittest.TestCase):
    def test_orphans_are_runners_whose_parent_is_gone(self):
        with patch.object(orphans, '_run', return_value=listing(runner(1, 10, True), runner(2, 11, False, mb=6200))):
            found = orphans.orphans()
        self.assertEqual([o['pid'] for o in found], [2])
        self.assertEqual(found[0]['memory_mb'], 6200)
        self.assertEqual(found[0]['model_blob'], 'sha256-abcdef0123456789a')

    def test_a_single_process_and_empty_output_are_handled(self):
        with patch.object(orphans, '_run', return_value=json.dumps(runner(5, 9, False))):
            self.assertEqual([r['pid'] for r in orphans.list_runners()], [5])
        with patch.object(orphans, '_run', return_value=''):
            self.assertEqual(orphans.list_runners(), [])

    def test_runners_outside_the_ollama_install_are_never_listed(self):
        other = 'C:\\Tools\\llama.cpp\\llama-server.exe'
        with patch.object(orphans, '_run', return_value=listing(runner(7, 1, False, path=other))):
            self.assertEqual(orphans.list_runners(), [])

    def test_cleanup_reverifies_each_pid_and_only_kills_confirmed_orphans(self):
        calls = []

        class Done:
            returncode = 0

        def fake_run(command, **kwargs):
            calls.append(command)
            return Done()

        # 第一次列出两个孤儿；清理 pid 2 之前再次核对时 pid 3 已经有了活着的父进程（被接管）。
        # Two orphans at first; by the time pid 3 is re-verified it has a living parent again, so it must be skipped.
        listings = iter([listing(runner(2, 11, False), runner(3, 12, False)),
                         listing(runner(2, 11, False), runner(3, 12, False)),
                         listing(runner(2, 11, False), runner(3, 12, True))])
        with patch.object(orphans, '_run', side_effect=lambda *a, **k: next(listings)), patch.object(orphans.subprocess, 'run', side_effect=fake_run):
            result = orphans.clean_orphans()
        self.assertEqual(result['killed'], [2])
        self.assertEqual(result['skipped'], [3])
        self.assertEqual(result['freed_memory_mb'], 1000)
        self.assertEqual([c for c in calls if c[0] == 'taskkill'], [['taskkill', '/F', '/PID', '2']])

    def test_bad_process_output_is_a_clear_error_not_a_crash(self):
        from masa.domain.models import MasaError
        with patch.object(orphans, '_run', return_value='not json'):
            with self.assertRaises(MasaError):
                orphans.list_runners()


if __name__ == '__main__':
    unittest.main()
