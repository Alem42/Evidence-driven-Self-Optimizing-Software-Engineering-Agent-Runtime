"""Loopback-only HTTP transport. No shell endpoints or arbitrary file serving."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sqlite3
import socket
import os
from urllib.parse import urlsplit
import webbrowser

from masa.domain.models import MasaError
from masa.application.console import Console


STATIC = Path(__file__).parent / "static"


class LocalHTTPServer(ThreadingHTTPServer):
    """独占本机端口，避免 Windows 把请求分发给多个不同版本。 Own the loopback port exclusively across versions."""
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        """Windows 使用独占绑定；同端口第二个服务必须报错。 Reject a second listener on the same port on Windows."""
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(console, port=8765):
    token = secrets.token_urlsafe(32)

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
            if origin and origin != f"http://{host}":
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return False
            return not api or secrets.compare_digest(self.headers.get("X-MASA-Token", "").encode(), token.encode())

        def do_GET(self):
            self.dispatch(False)

        def do_POST(self):
            self.dispatch(True)

        def dispatch(self, write):
            path = urlsplit(self.path).path
            if not self.check_access(path.startswith("/api/")):
                self.send(403, {"error": "local session access denied"})
                return
            try:
                if not write and path in {"/", "/app.js", "/style.css"}:
                    name, mime = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                                  "/style.css": ("style.css", "text/css")}[path]
                    data = (STATIC / name).read_bytes().replace(b"__SESSION_TOKEN__", token.encode())
                    self.send(200, data, mime + "; charset=utf-8")
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
                elif len(parts)==3 and parts[:2]==['api','projects'] and not write:
                    result = console.projects(parts[2])
                elif path == "/api/settings":
                    result = console.settings.save(body) if write else console.settings.public()
                elif path == '/api/settings/test' and write:
                    result = console.settings.test(body.get('id') or console.settings.active_id)
                elif path == '/api/generate' and write:
                    result = console.generate(body)
                elif path == '/api/projects/plan' and write:
                    result = console.start_autonomous_project_job(body) if body.get('auto_verify') else console.start_project_job(body) if body.get('background') else console.plan_project(body)
                elif len(parts)==3 and parts[:2]==['api','jobs'] and not write:
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
                        elif action == 'rerun':
                            result = console.rerun(rid)
                        elif action == 'approve-project':
                            result = console.approve_project(rid, body)
                        elif action == 'generate-project':
                            result = console.start_project_job(body,rid) if body.get('background') else console.generate_project(rid, body)
                        elif action == 'resume-project':
                            result = console.start_project_job({**body,'resume_project':True},rid)
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
            except (OSError, sqlite3.Error):
                self.send(500, {"error": "local storage or tool access failed"})

    return LocalHTTPServer(("127.0.0.1", port), Handler)


def serve(state_dir, runner, go, project, port=8765, open_browser=False):
    if not 0 <= port <= 65535:
        raise MasaError("port must be 0..65535")
    if not all((STATIC / name).is_file() for name in ("index.html", "app.js", "style.css")):
        raise MasaError("frontend missing: run scripts/build-ui.ps1 first")
    console = Console(state_dir, runner, go, project)
    server = make_server(console, port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"MASA console: {url}\nLocal-only; Ctrl+C stops the console and cancels its active run.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        console.close()
