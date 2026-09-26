"""真实 Go 与进程退出恢复测试。 Real-Go and abrupt-process recovery tests."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from masa.adapters.sqlite import Store
from masa.workspace import manifest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'examples/go-todo'


@unittest.skipUnless(os.name == 'nt' and (ROOT / '.tools/bin/masa-runner.exe').exists(), 'built Windows runner required')
class PatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        """为 CLI 流程分配独立状态目录。 Allocate an isolated state directory for CLI flows."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = manifest(SOURCE)
        self.patch_file = self.root / 'patch.json'
        subprocess.run([sys.executable, str(ROOT / 'scripts/demo_patch.py'), '--output', str(self.patch_file)],
                       cwd=ROOT, check=True, capture_output=True)

    def tearDown(self):
        """验证源仓库未变并清理副本。 Verify source immutability and clean copies."""
        self.assertEqual(manifest(SOURCE), self.original)
        self.temp.cleanup()

    def cli(self, *args):
        """运行真实 CLI 并解析最终结果。 Run the actual CLI and parse its final result."""
        result = subprocess.run([sys.executable, '-m', 'masa', '--state-dir', str(self.root / 'state'), *args],
                                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=90)
        return result, json.loads(result.stdout)

    def test_failing_baseline_then_patched_go_checks(self):
        """证明原始缺陷失败，修复通过且测试不变。 Prove baseline failure and repaired success with unchanged tests."""
        process, baseline = self.cli('run', '--repo', str(SOURCE))
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(baseline['status'], 'failed')
        for operation in ('go_test', 'go_vet', 'go_fmt_check'):
            process, result = self.cli('run', '--repo', str(SOURCE), '--operation', operation, '--patch', str(self.patch_file))
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(result['tool_calls'], 2)

    def test_process_death_after_first_file_then_real_resume(self):
        """真实进程消失后恢复部分写入并验证。 Recover partial writes after actual process death and verify."""
        code = '''
import json, os, sys
from pathlib import Path
from masa.adapters.sqlite import Store
from masa.adapters.runner import Runner
from masa.domain import Budget
from masa.runtime import Runtime
from masa.patching import Patches
root, state, proposal = map(Path, sys.argv[1:])
store = Store(state)
runtime = Runtime(store, Runner(root/'.tools/bin/masa-runner.exe', root/'.tools/go/bin/go.exe'))
rid = runtime.create(root/'examples/go-todo', 'crash recovery', Budget())
print(rid, flush=True)
patches = Patches(store)
replace = patches._replace
def crash(*args):
    # 写完首文件后立即退出，OS 释放锁。 Exit after the first write; the OS releases the lock.
    replace(*args)
    os._exit(91)
patches._replace = crash
patches.apply(rid, json.loads(proposal.read_text(encoding='utf-8')))
'''
        process = subprocess.run([sys.executable, '-c', code, str(ROOT), str(self.root / 'state'), str(self.patch_file)],
                                 cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(process.returncode, 91, process.stderr)
        rid = process.stdout.strip()
        resumed, result = self.cli('resume', rid)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['tool_calls'], 2)
        store = Store(self.root / 'state')
        try:
            self.assertEqual(len(store.tools(rid)), 1)
            self.assertEqual(sum(e['type']=='patch_committed' for e in store.events(rid)), 1)
        finally:
            store.close()
