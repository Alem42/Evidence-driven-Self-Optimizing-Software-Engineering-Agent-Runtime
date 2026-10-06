"""Bounded stdio protocol with explicit cancellation and parent-loss cleanup."""

from masa.infrastructure.proc import NEW_SESSION, NO_WINDOW, kill_group
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from masa.domain.models import MasaError, canonical


class Runner:
    def __init__(self, executable: Path, go_executable: Path):
        self.executable = executable.resolve()
        self.go_executable = go_executable.resolve()
        if not self.executable.is_file() or not self.go_executable.is_file():
            raise MasaError("runner or Go binary missing; run scripts/build.ps1 first")

    def execute(self, request: dict, workspace: Path, cancelled) -> dict:
        allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
                   "LOCALAPPDATA", "GOROOT", "GOCACHE", "GOMODCACHE", "GOPATH"}
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env["GOROOT"] = str(self.go_executable.parent.parent)
        proc = subprocess.Popen([str(self.executable), "--workspace", str(workspace), "--go", str(self.go_executable)],
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
