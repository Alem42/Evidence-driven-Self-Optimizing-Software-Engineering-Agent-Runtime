"""看门狗（最小实现）：把 `masa serve` 当子进程运行，异常退出就按退避重新拉起，并把 崩溃/重启/放弃 写进运维事件流（也会触发 webhook）。
The watchdog (minimal): runs `masa serve` as a child process, restarts it with backoff after an abnormal exit and writes crash / restart / give-up into the ops event feed (the webhook fires too).

它只负责“服务活着”。服务重启后任务怎么办，由恢复分类决定（见 application/ops.py）：结果已落账本的调用复用并继续；不确定的付费调用暂停并列入“待处理”，不重发；
`--auto-resume` 只恢复没有不确定付费调用的中断任务。干净退出（退出码 0，例如 Ctrl+C 或 stop）不会被重启。
It only keeps the service alive. What happens to the jobs afterwards follows the recovery classes (application/ops.py): calls whose result is in the ledger are reused; uncertain paid calls pause as pending and are never resent;
`--auto-resume` resumes only interrupted jobs without an uncertain paid call. A clean exit (code 0, e.g. Ctrl+C or stop) is not restarted.
"""
import os
import subprocess
import sys
import time

from masa.application.ops import EventSink


def run(state_dir, serve_args, *, auto_resume=False, max_restarts=10, window_seconds=3600, backoff_cap=60, sleep=time.sleep, clock=time.time, popen=subprocess.Popen):
    """返回退出码：0 = 服务干净退出；1 = 放弃（一小时内重启太多次）。 Returns 0 after a clean exit of the service and 1 when it gave up (too many restarts within the window)."""
    sink = EventSink(state_dir, clock)
    restarts, total = [], 0
    while True:
        env = {**os.environ, 'MASA_RESTARTS': str(total), **({'MASA_AUTO_RESUME': '1'} if auto_resume else {})}
        command = [sys.executable, '-m', 'masa', '--state-dir', str(state_dir), 'serve', *serve_args]
        sink.emit('watchdog_started_service' if total == 0 else 'service_restarted', f'restart #{total}' if total else 'first start')
        process = popen(command, env=env)
        try:
            code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            sink.emit('watchdog_stopped', 'stopped by the operator')
            return 0
        if code == 0:
            sink.emit('service_stopped', 'clean exit; not restarted')
            return 0
        sink.emit('service_crashed', f'exit code {code}', None, 'warn')
        restarts = [t for t in restarts if clock() - t < window_seconds]
        if len(restarts) >= max_restarts:
            sink.emit('watchdog_gave_up', f'{len(restarts)} restarts within {window_seconds // 60} minutes; a human needs to look', None, 'error')
            return 1
        sleep(min(backoff_cap, 2 ** len(restarts)))
        restarts.append(clock())
        total += 1
