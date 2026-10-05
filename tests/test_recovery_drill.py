"""崩溃恢复演练：真启动子进程、用 TerminateProcess 硬杀（没有任何清理机会）、再用全新进程恢复。
Crash-recovery drill: a real child process is hard-killed (no cleanup) and a brand-new process resumes the same state directory.

验证的是持久化与恢复契约，而不是“看起来能恢复”：
  · 已完成的模型调用不会被重复（跨进程计数）；
  · 路由状态（失败链、pending_fix、预算）跨崩溃保留；
  · 免费本地调用结果未知时作为新尝试重试；付费云调用永远不会被静默重放，而是明确停下。
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHILD = HERE / 'drill_child.py'


def run_child(state, phase, kill, wait_for_block=False, timeout=90):
    process = subprocess.Popen([sys.executable, str(CHILD), str(state), phase, kill], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not wait_for_block:
        out, err = process.communicate(timeout=timeout)
        return process.returncode, out, err
    marker = state / 'drill-blocked'
    deadline = time.time() + timeout
    while time.time() < deadline and not marker.exists():
        if process.poll() is not None:
            out, err = process.communicate()
            raise AssertionError(f'child exited before blocking: {out}\n{err}')
        time.sleep(0.1)
    if not marker.exists():
        process.kill()
        raise AssertionError('child never reached the kill point')
    process.kill()  # Windows: TerminateProcess，Unix: SIGKILL —— 无清理 / no cleanup
    process.wait(timeout=15)
    process.stdout.close()
    process.stderr.close()
    return process.returncode, '', ''


def result_of(out, err=''):
    lines = [line for line in out.splitlines() if line.startswith('RESULT ')]
    if not lines:
        raise AssertionError(f'no RESULT line.\nstdout: {out}\nstderr: {err}')
    return json.loads(lines[-1][7:])


def calls(state):
    return [line.split(' ', 1)[1] for line in (state / 'drill-calls.log').read_text().splitlines()]


class RecoveryDrillTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name)

    def test_crash_during_verification_resumes_without_repeating_model_calls_and_still_escalates(self):
        """本地修复完成后，在验证中途被硬杀；新进程恢复：不重复任何模型调用，路由状态延续，随后升级到云并成功。"""
        code, _, _ = run_child(self.state, 'start', 'verify', wait_for_block=True)
        self.assertNotEqual(code, 0)  # 被杀 / it was killed
        before = calls(self.state)
        self.assertEqual(before.count('local project_repair'), 1)
        self.assertEqual(before.count('cloud project_repair'), 0)

        code, out, err = run_child(self.state, 'resume', 'none')
        result = result_of(out, err)
        after = calls(self.state)
        # 每个模型调用恰好一次：本地规划/测试/生成/修复各 1，云修复 1。 Every model call happened exactly once overall.
        for purpose in ('project_planner', 'project_tester', 'project_developer'):
            self.assertEqual(after.count(f'local {purpose}'), 1, purpose)
        self.assertEqual(after.count('local project_repair'), 1)
        self.assertEqual(after.count('cloud project_repair'), 1)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['final_run_status'], 'succeeded', result)
        self.assertEqual(result['escalations'], 1)  # 失败链跨崩溃保留，所以还是升级了 / the chain survived the crash
        self.assertEqual(next(v for k, v in result['route_history'].items() if k.startswith('fix'))[0]['candidate'], 'local')

    def test_a_free_local_repair_call_lost_in_a_crash_is_retried_as_a_new_attempt(self):
        """本地修复请求已发出、进程被硬杀：免费调用作为同一次调用的新尝试重试（旧尝试标为被放弃），随后正常升级并成功。"""
        code, _, _ = run_child(self.state, 'start', 'model', wait_for_block=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(calls(self.state).count('local project_repair'), 1)
        code, out, err = run_child(self.state, 'resume', 'none')
        result = result_of(out, err)
        after = calls(self.state)
        self.assertEqual(after.count('local project_repair'), 2, 'the lost free call is attempted again')
        self.assertEqual(after.count('cloud project_repair'), 1)
        self.assertEqual(result['final_run_status'], 'succeeded', result)
        self.assertEqual(result['abandoned'], 1)  # 报告里能看到被放弃的那次 / the report shows the abandoned attempt

    def test_a_free_local_generation_call_lost_in_a_crash_is_retried_and_finished_work_is_kept(self):
        """本地生成代码的请求中途丢失：已完成的规划/测试方案不重做，生成作为新尝试重试。"""
        code, _, _ = run_child(self.state, 'start', 'generation', wait_for_block=True)
        self.assertNotEqual(code, 0)
        code, out, err = run_child(self.state, 'resume', 'none')
        result = result_of(out, err)
        after = calls(self.state)
        self.assertEqual(after.count('local project_planner'), 1)
        self.assertEqual(after.count('local project_tester'), 1)
        self.assertEqual(after.count('local project_developer'), 2)  # 丢失的一次 + 重试 / the lost call plus its retry
        self.assertEqual(result['final_run_status'], 'succeeded', result)
        self.assertEqual(result['abandoned'], 1)

    def test_a_paid_cloud_call_lost_in_a_crash_is_never_replayed(self):
        """付费云调用结果未知：永远不自动重放，明确停下。这是免费重试的边界。"""
        code, _, _ = run_child(self.state, 'start', 'cloudrepair', wait_for_block=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(calls(self.state).count('cloud project_repair'), 1)
        code, out, err = run_child(self.state, 'resume', 'none')
        result = result_of(out, err)
        after = calls(self.state)
        self.assertEqual(after.count('cloud project_repair'), 1, 'a paid request must never be replayed silently')
        self.assertNotEqual(result.get('final_run_status'), 'succeeded', result)
        self.assertIn('unavailable', str(result.get('error')))


if __name__ == '__main__':
    unittest.main()
