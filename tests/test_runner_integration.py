import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from masa.infrastructure.runner import Runner


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / ".tools/bin/masa-runner.exe"
GO = ROOT / ".tools/go/bin/go.exe"


def alive(pid):
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.OpenProcess.restype = ctypes.c_void_p
    dll.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    dll.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    dll.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = dll.OpenProcess(0x100000, 0, pid)
    if not handle:
        return False
    try:
        return dll.WaitForSingleObject(handle, 0) == 258
    finally:
        dll.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt" and RUNNER.exists(), "built Windows runner required")
class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "repo"
        shutil.copytree(ROOT / "tests/fixtures/go-pass", self.workspace)
        self.runner = Runner(RUNNER, GO)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, operation="go_test", timeout=30000, output=65536):
        return {"protocol_version": 1, "request_id": uuid.uuid4().hex,
                "snapshot_id": "integration", "operation": operation,
                "timeout_ms": timeout, "max_output_bytes": output}

    def test_real_test_vet_and_format(self):
        for op in ("go_test", "go_vet", "go_fmt_check"):
            with self.subTest(operation=op):
                result = self.runner.execute(self.request(op), self.workspace, lambda: False)
                self.assertEqual((result["status"], result["exit_code"]), ("completed", 0), result)

    def test_failed_tests_are_completed_nonzero(self):
        (self.workspace / "sum.go").write_text("package fixture\nfunc Sum(a,b int) int {return 0}\n")
        result = self.runner.execute(self.request(), self.workspace, lambda: False)
        self.assertEqual(result["status"], "completed", result)
        self.assertNotEqual(result["exit_code"], 0)

    def test_format_failure_is_read_only(self):
        path = self.workspace / "sum.go"
        original = "package fixture\nfunc Sum(a,b int) int {return a+b}\n"
        path.write_text(original)
        result = self.runner.execute(self.request("go_fmt_check"), self.workspace, lambda: False)
        self.assertEqual(result["exit_code"], 1, result)
        self.assertEqual(path.read_text(), original)

    def test_large_output_and_secret_environment(self):
        code = '''package fixture
import ("fmt"; "os"; "strings"; "testing")
func TestOutput(t *testing.T) {
 if os.Getenv("MASA_TEST_SECRET") != "" { t.Fatal("secret leaked") }
 fmt.Println(strings.Repeat("A", 1000000))
 fmt.Fprintln(os.Stderr, strings.Repeat("B", 1000000))
}
'''
        (self.workspace / "output_test.go").write_text(code)
        from unittest.mock import patch
        with patch.dict(os.environ, {"MASA_TEST_SECRET": "synthetic-test-value"}):
            result = self.runner.execute(self.request(output=1024), self.workspace, lambda: False)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["stdout"].encode()) + len(result["stderr"].encode()), 1024)

    def prepare_tree(self):
        (self.workspace / "tree_test.go").write_text('''package fixture
import ("encoding/json"; "os"; "os/exec"; "testing"; "time")
func TestChild(t *testing.T) { if os.Getenv("TREE_CHILD") == "1" { time.Sleep(60*time.Second) } }
func TestTree(t *testing.T) {
 c:=exec.Command(os.Args[0], "-test.run=^TestChild$")
 c.Env=append(os.Environ(), "TREE_CHILD=1")
 if err:=c.Start(); err!=nil { t.Fatal(err) }
 data,_:=json.Marshal([]int{os.Getpid(),c.Process.Pid})
 if err:=os.WriteFile("pids.json",data,0600); err!=nil { t.Fatal(err) }
 time.Sleep(60*time.Second)
}
''')
        # Precompile without executing, so timeout tests measure execution not cold compile.
        subprocess.run([str(GO), "test", "-c", "-o", str(Path(self.temp.name) / "fixture.test.exe")],
                       cwd=self.workspace, check=True, capture_output=True, timeout=30)

    def assert_tree_stopped(self):
        self.assertTrue((self.workspace / "pids.json").exists(), "test never reached subprocess creation")
        pids = json.loads((self.workspace / "pids.json").read_text())
        until = time.monotonic() + 3
        while any(alive(p) for p in pids) and time.monotonic() < until:
            time.sleep(0.05)
        self.assertFalse(any(alive(p) for p in pids), f"leaked process tree: {pids}")

    def test_explicit_cancel_kills_parent_and_child(self):
        self.prepare_tree()
        result = self.runner.execute(self.request(), self.workspace,
                                     lambda: (self.workspace / "pids.json").exists())
        self.assertEqual(result["status"], "cancelled", result)
        self.assert_tree_stopped()

    def test_timeout_kills_parent_and_child(self):
        self.prepare_tree()
        result = self.runner.execute(self.request(timeout=5000), self.workspace, lambda: False)
        self.assertEqual(result["status"], "timeout", result)
        self.assert_tree_stopped()

    def test_abrupt_coordinator_death_kills_tree(self):
        self.prepare_tree()
        proc = subprocess.Popen([str(RUNNER), "--workspace", str(self.workspace), "--go", str(GO)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            proc.stdin.write((json.dumps(self.request()) + "\n").encode())
            proc.stdin.flush()
            until = time.monotonic() + 20
            while not (self.workspace / "pids.json").exists() and time.monotonic() < until and proc.poll() is None:
                time.sleep(0.05)
            self.assertTrue((self.workspace / "pids.json").exists())
            proc.kill()
            proc.wait(timeout=5)
            self.assert_tree_stopped()
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)

    def test_cli_pause_resume_real_go(self):
        state = Path(self.temp.name) / "state"
        command = [sys.executable, "-m", "masa", "--state-dir", str(state)]
        first = subprocess.run(command + ["run", "--repo", str(self.workspace), "--pause-after", "1"],
                               check=True, capture_output=True, text=True, timeout=40)
        paused = json.loads(first.stdout)
        self.assertEqual(paused["status"], "paused")
        second = subprocess.run(command + ["resume", paused["id"]], check=True, capture_output=True, text=True, timeout=10)
        finished = json.loads(second.stdout)
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["tool_calls"], 1)
        report = subprocess.run(command + ["report", paused["id"]], check=True, capture_output=True, text=True)
        self.assertIn("scripted-v1", report.stdout)
