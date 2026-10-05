"""Loopback-only HTTP transport. No shell endpoints or arbitrary file serving."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sqlite3
import socket
import os
from urllib.parse import urlsplit

from masa.domain.models import MasaError
from masa.application.console import Console
from masa.infrastructure.locking import owner_lock


# 前端独立部署（Vite dev/preview 或任意静态服务器），后端只提供 API；默认放行本机 Vite 端口。
# The frontend is a separate app; this server is API-only and allows loopback dev origins by default.
DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173")


class LocalHTTPServer(ThreadingHTTPServer):
    """独占本机端口，避免 Windows 把请求分发给多个不同版本。 Own the loopback port exclusively across versions."""
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        """Windows 使用独占绑定；同端口第二个服务必须报错。 Reject a second listener on the same port on Windows."""
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(console, port=8765, origins=DEFAULT_ORIGINS):
    token = secrets.token_urlsafe(32)
    allowed_origins = frozenset(o.rstrip("/") for o in origins)

    class Handler(BaseHTTPRequestHandler):
        server_version = "MASA"

        def log_message(self, *args):
            pass  # Never log request bodies, credentials or token-bearing URLs.

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            origin = self.headers.get("Origin")
            if origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(raw)

        def check_access(self, api=False):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed:
                return False
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{host}" and origin not in allowed_origins:
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return False
            return not api or secrets.compare_digest(self.headers.get("X-MASA-Token", "").encode(), token.encode())

        def do_OPTIONS(self):
            """CORS 预检：只对白名单来源放行。 CORS preflight, allowed origins only."""
            origin = self.headers.get("Origin")
            if origin not in allowed_origins or not self.check_access(False):
                self.send(403, {"error": "origin not allowed"})
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "X-MASA-Token, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            self.dispatch(False)

        def do_POST(self):
            self.dispatch(True)

        def dispatch(self, write):
            path = urlsplit(self.path).path
            if not self.check_access(path.startswith("/api/") and path != "/api/session"):
                self.send(403, {"error": "local session access denied"})
                return
            try:
                if path == "/api/session" and not write:
                    # 令牌只发给白名单来源的前端页面，其他来源即使 Host 正确也拿不到。
                    # Only allow-listed frontend origins can obtain the session token.
                    if self.headers.get("Origin") not in allowed_origins:
                        self.send(403, {"error": "origin not allowed"})
                        return
                    self.send(200, {"token": token, "version": "api-v1"})
                    return
                body = {}
                if write:
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise MasaError("JSON request required")
                    size = int(self.headers.get("Content-Length", "0"))
                    limit = 2000000 if path.endswith('/approve-project-code') else 65536
                    if not 0 < size <= limit:
                        raise MasaError("invalid request body size")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise MasaError("JSON object required")
                parts = path.strip("/").split("/")
                if path == "/api/bootstrap" and not write:
                    result = console.bootstrap()
                elif path == '/api/projects' and not write:
                    result = console.projects()
                elif len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='report' and not write:
                    result = console.project_report(parts[2])
                elif len(parts)==3 and parts[:2]==['api','projects'] and not write:
                    result = console.projects(parts[2])
                elif path == "/api/settings":
                    result = console.settings.save(body) if write else console.settings.public()
                elif path == '/api/triage' and write:
                    result = console.triage(body)
                elif path == '/api/routing':
                    result = console.settings.save_routing(body) if write else console.settings.routing()
                elif path == '/api/settings/order' and write:
                    result = console.settings.reorder(body.get('order'), body.get('levels'))
                elif path == '/api/accounts' and not write:
                    result = {'accounts': console.settings.accounts()}
                elif len(parts) == 4 and parts[:2] == ['api', 'accounts'] and parts[3] == 'models' and not write:
                    result = {'models': console.settings.account_models(parts[2])}
                elif len(parts) == 4 and parts[:2] == ['api', 'accounts'] and parts[3] == 'balance' and not write:
                    result = console.settings.account_balance(parts[2])
                elif len(parts) == 4 and parts[:2] == ['api', 'accounts'] and parts[3] == 'add-models' and write:
                    result = console.settings.add_models(parts[2], body.get('models'))
                elif len(parts) == 4 and parts[:2] == ['api', 'accounts'] and parts[3] == 'select' and write:
                    result = console.settings.select_models(parts[2], body.get('models'))
                elif path == '/api/settings/test' and write:
                    result = console.settings.test(body.get('id') or console.settings.active_id)
                elif path == '/api/ollama' and not write:
                    from masa.infrastructure.ollama import OllamaControl
                    result = OllamaControl().catalog()
                elif path == '/api/ollama/action' and write:
                    result = console.ollama_action(body)
                elif path == '/api/ollama/orphans' and not write:
                    from masa.infrastructure import orphans
                    result = {'runners': orphans.list_runners()}
                elif path == '/api/ollama/orphans/clean' and write:
                    from masa.infrastructure import orphans
                    result = orphans.clean_orphans()
                elif path == '/api/workflows/fix-v1' and not write:
                    from masa.application.workflows import FIX_V1
                    result = FIX_V1
                elif path == '/api/roles' and not write:
                    # 角色清单来自 RoleSpec 注册表（只读，不含提示词全文）：前端的阶段标签与顺序据此显示。
                    # The role list from the RoleSpec registry (read-only, never the prompts): the UI derives stage labels and order from it.
                    from masa.roles import registry
                    result = {'roles': registry.current().public()}
                elif path == '/api/bench/tasks' and not write:
                    result = console.bench_tasks()
                elif path == '/api/bench/status' and not write:
                    result = console.bench_status()
                elif path == '/api/bench/results' and not write:
                    result = console.bench_results()
                elif len(parts) == 4 and parts[:3] == ['api', 'bench', 'results'] and not write:
                    result = console.bench_result(parts[3])
                elif path == '/api/bench/start' and write:
                    result = console.bench_start(body)
                elif path == '/api/bench/stop' and write:
                    result = console.bench_stop()
                elif path == '/api/hardware' and not write:
                    result = console.hardware.snapshot()
                elif path == '/api/diagnostics' and not write:
                    result = console.diagnostics.recent()
                elif path == '/api/generate' and write:
                    result = console.generate(body)
                elif path == '/api/projects/plan' and write:
                    result = console.start_autonomous_project_job(body) if body.get('auto_verify') else console.start_project_job(body) if body.get('background') else console.plan_project(body)
                elif len(parts)==3 and parts[:2]==['api','jobs'] and not write:
                    if parts[2] not in console.jobs:
                        self.send(404, {'error':'任务不存在，请查看保存的项目记录。'}); return
                    result = console.project_job(parts[2])
                elif len(parts)==4 and parts[:2]==['api','jobs'] and parts[3]=='resume' and write:
                    result = console.start_autonomous_project_job({},resume_job=parts[2])
                elif path == "/api/runs":
                    result = console.create(body) if write else {"runs": console.list_runs(), "active_run": console.active}
                elif len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                    rid = parts[2]
                    if len(parts) == 3 and not write:
                        result = console.detail(rid)
                    elif len(parts) == 5 and parts[3] == "artifacts" and not write:
                        result = {"artifact": console.artifact(rid, parts[4])}
                    elif len(parts) == 4 and parts[3] == "report" and not write:
                        result = {"markdown": console.report(rid)}
                    elif len(parts) == 4 and parts[3] == 'results' and not write:
                        result = console.results(rid)
                    elif len(parts) == 4 and parts[3] == 'logs' and not write:
                        result = console.project_logs(rid)
                    elif len(parts) == 4 and write:
                        action = parts[3]
                        if action == "resume":
                            result = console.resume(rid, body.get('pause_after'))
                        elif action == 'auto-fix':
                            result = console.auto_fix(rid, body)
                        elif action == 'rerun':
                            result = console.rerun(rid)
                        elif action == 'approve-project':
                            result = console.approve_project(rid, body)
                        elif action == 'generate-project':
                            result = console.start_project_job(body,rid) if body.get('background') else console.generate_project(rid, body)
                        elif action == 'resume-project':
                            result = console.start_project_job({**body,'resume_project':True},rid)
                        elif action == 'answer-clarification':
                            result = console.answer_clarification(rid,body)
                        elif action == 'review-project-sources':
                            result = console.review_project_sources(rid,body)
                        elif action == 'run-application':
                            result = console.start_application(rid,body)
                        elif action == 'stop-application':
                            result = console.stop_application(rid)
                        elif action == 'repair-project':
                            result = console.start_project_job({**body,'repair':True},rid) if body.get('background') else console.repair_project(rid,body)
                        elif action == 'revise-project-tests':
                            result = console.start_project_job({**body,'test_revision':True},rid) if body.get('background') else console.revise_project_tests(rid,body)
                        elif action == 'format-project-tests':
                            result = console.format_project_tests(rid)
                        elif action == 'open-workspace':
                            result = console.open_workspace(rid)
                        elif action == 'approve-project-code':
                            result = console.approve_project_code(rid, body)
                        elif action == 'review-code':
                            result = console.review_code(rid, body)
                        elif action in {"pause", "cancel"}:
                            result = console.control(rid, action)
                        elif action == "revise":
                            result = console.create(body, parent=rid)
                        else:
                            self.send(404, {"error": "unknown action"}); return
                    else:
                        self.send(404, {"error": "not found"}); return
                else:
                    self.send(404, {"error": "not found"}); return
                self.send(200, result)
            except (MasaError, ValueError, TypeError) as exc:
                self.send(400, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                return  # The client left; no second response or request replay.
            except Exception as exc:
                # 用固定路由类别记录调用位置，不把请求/密钥/异常文字落日志。
                # Record safe frame locations, never requests, credentials or raw exception text.
                group = next((g for g in ('projects','runs','jobs','settings','ollama','hardware','diagnostics','bootstrap')
                              if path.startswith('/api/'+g)), 'other')
                request_id = console.diagnostics.record(('POST' if write else 'GET')+' /api/'+group, exc)
                self.send(500, {"error": "后台处理失败，请在本地模型控制的后台诊断中查看。", "request_id": request_id})

    return LocalHTTPServer(("127.0.0.1", port), Handler)


def serve(state_dir, runner, go, project, port=8765, origins=DEFAULT_ORIGINS):
    """先取得状态目录独占权再恢复任务，失败启动也释放资源。 Own state before recovery and clean up failed starts."""
    if not 0 <= port <= 65535:
        raise MasaError("port must be 0..65535")
    # Console 初始化会恢复 Jobs；第二个服务必须在触碰任务状态之前被拒绝。
    # Console initialization recovers Jobs; reject another server before it mutates jobs.
    # 独立锁不与 Runtime 的短期工具锁混用，所有端口共享同一目录独占权。
    # A separate lifetime lock leaves Runtime tool locks free and covers every port.
    with owner_lock(Path(state_dir).resolve() / "console.lock"):
        console = Console(state_dir, runner, go, project)
        server = None
        try:
            server = make_server(console, port, origins)
            url = f"http://127.0.0.1:{server.server_port}"
            print(f"MASA API: {url}\nAllowed frontend origins: {', '.join(sorted(origins))}", flush=True)
            print("Local-only. Start the frontend separately: cd frontend && npm run dev. Ctrl+C stops the API and cancels its active run.", flush=True)
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            # 端口绑定或 server_close 失败，也必须关闭 Console，再由 OS 释放锁。
            # Close Console even after bind/server-close failures, then release the OS lock.
            try:
                if server is not None:
                    server.server_close()
            finally:
                console.close()
