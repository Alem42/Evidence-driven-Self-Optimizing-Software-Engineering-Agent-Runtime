"""Bounded stdio protocol with explicit cancellation and parent-loss cleanup."""

from masa.infrastructure.proc import NEW_SESSION, NO_WINDOW, kill_group
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time

from masa.domain.models import MasaError, canonical
from masa.languages.registry import registry


class Runner:
    def __init__(self, executable: Path, go_executable: Path | None = None):
        self.executable = executable.resolve()
        # go_executable 是旧名字（账本画像与旧测试都用它），只有对应的语言包会读它；其它语言的工具链由各自的包配置。
        # go_executable is the historical name (ledger profiles and old tests use it) and only its pack reads it; other languages' toolchains are configured by their packs.
        self.go_executable = go_executable.resolve() if go_executable else None
        if not self.executable.is_file() or (self.go_executable is not None and not self.go_executable.is_file()):
            raise MasaError("runner or Go binary missing; run scripts/build.ps1 first")

    def execute(self, request: dict, workspace: Path, cancelled) -> dict:
        # 操作怎么执行由语言包生成的清单决定；请求里只有操作名。 How an operation runs comes from a pack-generated table; the request carries only the operation name.
        pack = registry.pack_of_operation(request["operation"])
        ops = pack.runner_ops(self)
        # 运行器进程自己的环境：只带语言包允许传给被测代码的变量（运行器从自己的环境里读它们）。 The runner process's own environment: only what the pack lets the code under test see (the runner reads them from its own environment).
        allowed = {name.upper() for name in ops["env"]["allow"]}
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update(pack.runner_env(self))
        handle, ops_path = tempfile.mkstemp(prefix="masa-ops-", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(ops, fh)
        proc = None
        try:
            return self._execute(request, workspace, cancelled, ops_path, env)
        finally:
            try:
                os.unlink(ops_path)
            except OSError:
                pass

    def _execute(self, request: dict, workspace: Path, cancelled, ops_path: str, env: dict) -> dict:
        proc = subprocess.Popen([str(self.executable), "--workspace", str(workspace), "--ops", ops_path],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **NO_WINDOW, **NEW_SESSION)
        output: queue.Queue = queue.Queue()
        limit = request["max_output_bytes"] * 12 + 65536

        def drain(stream, tag, cap):
            data = stream.read(cap + 1)
            if len(data) > cap:
                proc.kill()
            output.put((tag, data))

        threads = [threading.Thread(target=drain, args=(proc.stdout, "stdout", limit), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, "stderr", 65536), daemon=True)]
        for t in threads:
            t.start()
        sent_cancel = False
        deadline = time.monotonic() + request["timeout_ms"] / 1000 + 8
        try:
            proc.stdin.write((canonical(request) + "\n").encode())
            proc.stdin.flush()
            while proc.poll() is None:
                try:
                    should_cancel = cancelled()
                except KeyboardInterrupt:
                    should_cancel = True
                if should_cancel and not sent_cancel:
                    try:
                        proc.stdin.write((canonical({"cancel": request["request_id"]}) + "\n").encode())
                        proc.stdin.flush()
                    except BrokenPipeError:
                        pass
                    sent_cancel = True
                if time.monotonic() > deadline:
                    raise MasaError("runner_watchdog: tool result is uncertain")
                time.sleep(0.04)
            for t in threads:
                t.join(timeout=2)
            if any(t.is_alive() for t in threads):
                raise MasaError("runner pipe did not close")
            chunks = dict(output.get_nowait() for _ in range(2))
            if proc.returncode != 0 or len(chunks["stdout"]) > limit or len(chunks["stderr"]) > 65536:
                raise MasaError("runner process/protocol failed; result is uncertain")
            try:
                result = json.loads(chunks["stdout"])
            except (ValueError, UnicodeError) as exc:
                raise MasaError("invalid runner JSON") from exc
            if (not isinstance(result, dict) or result.get("protocol_version") != 1
                    or result.get("request_id") != request["request_id"]
                    or result.get("snapshot_id") != request["snapshot_id"]
                    or result.get("status") not in {"completed", "timeout", "cancelled", "rejected", "internal_error"}):
                raise MasaError("runner response identity/status mismatch")
            if result["status"] == "completed" and type(result.get("exit_code")) is not int:
                raise MasaError("runner omitted completed exit code")
            return result
        finally:
            if proc.poll() is None:
                proc.kill()  # Coordinator job teardown also kills its worker and children.
            proc.wait(timeout=5)
            # Linux：整组 kill，等价于 Windows 的 kill-on-close Job Object（没有它，超时后 go test 起的测试进程可能变成孤儿继续跑）。
            # Linux: kill the whole process group, the equivalent of the Windows kill-on-close Job Object (without it a test binary started by go test could outlive a timeout).
            kill_group(proc.pid)
            for t in threads:
                t.join(timeout=2)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except BrokenPipeError:
                    pass
