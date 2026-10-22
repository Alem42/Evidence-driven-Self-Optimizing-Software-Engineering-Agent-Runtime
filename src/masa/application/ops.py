"""无人值守的运维层（最小实现）：待处理队列、事件流、webhook、停滞检测、急停。看门狗（masa watch）和以后的手机通知等外部程序都接在这套 API 上。
The unattended-operation layer (minimal): the pending queue, the event feed, a webhook, stall detection and the emergency stop. The watchdog (`masa watch`) and later things such as phone notifications attach to this API.

恢复语义（不承诺“全部自动完成且零重复付费”）/ Recovery semantics (it does not promise "everything finishes automatically with no repeated payment")
  进程退出前，远端可能已受理付费请求而本地没有收到结果；看门狗能重新拉起服务，却无法凭空确定远端状态。所以恢复分三类：
  Before the process died the provider may have accepted a paid request while no result reached us; a watchdog can restart the service but cannot know the remote state. Recovery therefore has three classes:
    1. 结果已落账本的调用：复用并继续。 A call whose result is in the ledger: reuse it and go on.
    2. 不确定的付费调用：暂停，列入“待处理”，不重发。 An uncertain paid call: pause, list it as pending, never resend.
    3. 只有明确允许（策略 unknown_paid_calls = retry，或在待处理队列里对这一次调用选 retry）才发起新的付费尝试。 A new paid attempt only when explicitly allowed (policy unknown_paid_calls = retry, or a retry decision for that very call in the queue).
"""
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

from masa.domain.models import MasaError
from masa.infrastructure.store import Store
from masa.runtime import live

DEFAULTS = {'webhook_url': '', 'stall_minutes': 30, 'stall_action': 'notify'}
JOB_EVENTS = {'completed': ('job_completed', 'info'), 'failed': ('job_failed', 'warn'), 'interrupted': ('job_interrupted', 'warn'),
              'waiting_for_input': ('job_waiting_for_input', 'warn'), 'cancelled': ('job_cancelled', 'info')}
TERMINAL = {'completed', 'failed', 'cancelled'}


class EventSink:
    """事件流 + webhook。看门狗进程没有 Console，也用它写事件。 The event feed plus the webhook; the watchdog process has no Console and uses it too."""

    def __init__(self, root, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.path = self.root / 'ops.sqlite3'
        self.config_path = self.root / 'ops.json'
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS ops_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, level TEXT, run_id TEXT, message TEXT)')
            db.commit()
        self._webhook_failed_at = 0

    def config(self):
        try:
            saved = json.loads(self.config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        config = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS or k == 'token'}}
        if not config.get('token'):  # 外部程序（手机通知等）用的固定令牌：只在本机文件里，首次读取时生成 / a stable token for external programs, kept only in this local file and generated on first read
            config['token'] = secrets.token_hex(16)
            self.config_path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding='utf-8')
        return config

    def save_config(self, body):
        config = self.config()
        if 'webhook_url' in body:
            url = str(body['webhook_url'] or '').strip()
            if url and not url.startswith(('http://', 'https://')):
                raise MasaError('webhook_url must start with http:// or https://')
            config['webhook_url'] = url
        if 'stall_minutes' in body:
            if type(body['stall_minutes']) is not int or not 5 <= body['stall_minutes'] <= 1440:
                raise MasaError('stall_minutes must be an integer in 5..1440')
            config['stall_minutes'] = body['stall_minutes']
        if 'stall_action' in body:
            if body['stall_action'] not in ('notify', 'stop'):
                raise MasaError('stall_action must be notify or stop')
            config['stall_action'] = body['stall_action']
        self.config_path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding='utf-8')
        return {k: v for k, v in config.items() if k != 'token'}

    def emit(self, kind, message='', run_id=None, level='info'):
        message = str(message)[:300]
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            cursor = db.execute('INSERT INTO ops_events(ts,kind,level,run_id,message) VALUES(?,?,?,?,?)', (self.clock(), kind, level, run_id, message))
            db.commit()
            event_id = cursor.lastrowid
        url = self.config().get('webhook_url')
        if url:
            threading.Thread(target=self._post, args=(url, {'id': event_id, 'kind': kind, 'level': level, 'run_id': run_id, 'message': message, 'ts': self.clock()}), daemon=True).start()
        return event_id

    def _post(self, url, event):
        """webhook：只送事件种类、编号和一句话，不含代码、提示词或密钥；失败不重试，只记一条（10 分钟内最多一条）。
        Webhook: only the kind, ids and one sentence - no code, prompts or keys; a failure is not retried and recorded at most once per ten minutes."""
        try:
            request = urllib.request.Request(url, data=json.dumps(event, ensure_ascii=False).encode('utf-8'), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=5):
                pass
        except Exception as exc:
            if self.clock() - self._webhook_failed_at > 600:
                self._webhook_failed_at = self.clock()
                with closing(sqlite3.connect(self.path, timeout=10)) as db:
                    db.execute('INSERT INTO ops_events(ts,kind,level,run_id,message) VALUES(?,?,?,?,?)', (self.clock(), 'webhook_failed', 'warn', None, str(exc)[:200]))
                    db.commit()

    def events(self, since=0, limit=200):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            rows = db.execute('SELECT id,ts,kind,level,run_id,message FROM ops_events WHERE id>? ORDER BY id LIMIT ?', (int(since), min(int(limit), 1000))).fetchall()
        return [dict(zip(('id', 'ts', 'kind', 'level', 'run_id', 'message'), row)) for row in rows]

    def last_id(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            return db.execute('SELECT COALESCE(MAX(id),0) FROM ops_events').fetchone()[0]


class Ops:
    def __init__(self, console, clock=time.time):
        self.console, self.clock = console, clock
        self.sink = EventSink(console.root, clock)
        self.started = clock()
        self.seen_jobs, self.seen_pending, self.stalled = {}, set(), {}
        self.announced = False

    # ───────────── 读模型 / read model ─────────────
    def worker_alive(self):
        thread = getattr(self.console, 'job_thread', None)
        return bool(thread and thread.is_alive())

    def pending(self):
        """需要人（或明确策略）处理的事项。 Items that need a human (or an explicit policy)."""
        items = []
        blocked = {}
        store = Store(self.console.root)
        try:
            alive = self.worker_alive()
            try:
                rows = store.db.execute("SELECT run_id,purpose,invocation_id,started,route_ref FROM role_invocations WHERE status='running' ORDER BY started").fetchall()
            except sqlite3.OperationalError:
                rows = []  # 还没有任何角色调用：账本里连这张表都没有 / no role call yet: the table does not even exist
            for row in rows:
                if alive and (live.get(row['run_id']) or self.clock() - row['started'] < 120):
                    continue  # 这个进程里正在进行的调用 / a call in flight in this process
                try:
                    route = store.read(row['route_ref'])
                except Exception:
                    continue
                if (route or {}).get('model_type') == 'local':
                    continue  # 免费本地调用会自动重试 / free local calls retry on their own
                granted = sum(1 for e in store.events(row['run_id']) if e['type'] == 'uncertain_call_resolved' and e['payload'].get('step_id') == row['purpose']
                              and e['payload'].get('invocation_id') == row['invocation_id'])
                used = sum(1 for e in store.events(row['run_id']) if e['type'] == 'model_abandoned' and e['payload'].get('step_id') == row['purpose']
                           and e['payload'].get('invocation_id') == row['invocation_id'] and str(e['payload'].get('reason', '')).startswith('explicit retry'))
                ident = f"{row['run_id']}:{row['purpose']}:{row['invocation_id']}"
                item = {'kind': 'uncertain_paid_call', 'id': ident, 'run_id': row['run_id'], 'step_id': row['purpose'], 'invocation_id': row['invocation_id'],
                        'model': (route or {}).get('model'), 'since': row['started'], 'retry_approved': granted > used,
                        'message': '请求发出后没有收到结果：远端可能已受理并计费。默认不重发；选择 retry 才会发起新的一次付费尝试。',
                        'actions': ['retry']}
                items.append(item)
                blocked.setdefault(row['run_id'], []).append(ident)
        finally:
            store.close()
        for ident, job in list(self.console.jobs.items()):
            if job.get('mode') != 'auto':
                continue
            if job.get('status') == 'interrupted':
                chain_blocked = [i for run, ids in blocked.items() for i in ids if run == job.get('run_id') or self._same_task(run, job.get('run_id'))]
                items.append({'kind': 'interrupted_job', 'id': ident, 'run_id': job.get('run_id'), 'phase': job.get('phase'), 'since': job.get('started'),
                              'message': job.get('note') or '服务重启后任务被中断。', 'blocked_by': chain_blocked, 'actions': ['resume'] if not chain_blocked else []})
            elif job.get('status') == 'waiting_for_input':
                items.append({'kind': 'needs_answer', 'id': ident, 'run_id': job.get('run_id'), 'phase': job.get('phase'), 'since': job.get('started'),
                              'message': '等待你回答澄清问题。', 'actions': []})
        return items

    def _same_task(self, run_a, run_b):
        if not run_a or not run_b:
            return False
        store = Store(self.console.root)
        try:
            from masa.application.projects import Projects
            runs = {r['id']: r for r in store.all_runs()}
            return run_a in runs and run_b in runs and Projects.root_id(run_a, runs) == Projects.root_id(run_b, runs)
        finally:
            store.close()

    def status(self):
        jobs = list(self.console.jobs.values())
        counts = {}
        for job in jobs:
            counts[job.get('status')] = counts.get(job.get('status'), 0) + 1
        active = next(({k: job.get(k) for k in ('run_id', 'phase', 'started', 'model')} for job in jobs if job.get('status') == 'running' and self.worker_alive()), None)
        return {'time': self.clock(), 'pid': os.getpid(), 'started': self.started, 'uptime_seconds': round(self.clock() - self.started),
                'restarts': int(os.environ.get('MASA_RESTARTS', '0') or 0), 'worker_alive': self.worker_alive(), 'active_job': active, 'jobs': counts,
                'pending': len(self.pending()), 'last_event_id': self.sink.last_id(), 'config': {k: v for k, v in self.sink.config().items() if k != 'token'}}

    # ───────────── 命令 / commands ─────────────
    def resolve(self, body):
        kind, action = body.get('kind'), body.get('action')
        if kind == 'uncertain_paid_call' and action == 'retry':
            for field in ('run_id', 'step_id', 'invocation_id'):
                if not isinstance(body.get(field), str) or not body[field]:
                    raise MasaError(f'{field} required')
            store = Store(self.console.root)
            try:
                store.run(body['run_id'])
                store.event(body['run_id'], 'uncertain_call_resolved', {'step_id': body['step_id'], 'invocation_id': body['invocation_id'], 'action': 'retry'})
            finally:
                store.close()
            self.sink.emit('pending_resolved', f"{body['step_id']} ({body['invocation_id']}): a new paid attempt is allowed once", body['run_id'])
            return {'resolved': True, 'note': '允许一次新的付费尝试；恢复这个任务后才会真正发出。'}
        if kind == 'interrupted_job' and action == 'resume':
            item = next((p for p in self.pending() if p['kind'] == 'interrupted_job' and p['id'] == body.get('id')), None)
            if item is None:
                raise MasaError('no such interrupted job')
            if item['blocked_by']:
                raise MasaError('this job has uncertain paid calls: choose retry for them first, or leave them paused')
            return self.console.start_autonomous_project_job({}, resume_job=item['id'])
        raise MasaError('unknown pending action')

    def stop_all(self):
        """急停：取消所有进行中的任务和评测，并释放本地模型。 Emergency stop: cancel every running job and the benchmark, and unload local models."""
        stopped = []
        for ident, job in list(self.console.jobs.items()):
            if job.get('status') == 'running' and job.get('run_id'):
                try:
                    self.console.control(job['run_id'], 'cancel')
                    stopped.append(job['run_id'])
                except Exception:
                    pass
        try:
            self.console.bench_stop()
        except Exception:
            pass
        self.console._release_local_models()
        self.sink.emit('stop_all', f'{len(stopped)} run(s) cancelled, local models unloaded', None, 'warn')
        return {'cancelled': stopped}

    # ───────────── 监视 / monitoring ─────────────
    def auto_resume(self):
        """看门狗用 --auto-resume 拉起服务时：只恢复没有不确定付费调用的中断任务，一次一个。 When the watchdog restarted the service with --auto-resume: resume interrupted jobs that have no uncertain paid call, one at a time."""
        if self.worker_alive():
            return None
        for item in self.pending():
            if item['kind'] == 'interrupted_job' and not item['blocked_by']:
                try:
                    self.console.start_autonomous_project_job({}, resume_job=item['id'])
                    self.sink.emit('job_auto_resumed', f"job {item['id']} resumed after the restart", item['run_id'])
                    return item['id']
                except MasaError as exc:
                    self.sink.emit('job_auto_resume_failed', str(exc), item['run_id'], 'warn')
        return None

    def tick(self):
        """每隔几秒调用一次：把状态变化写成事件。 Called every few seconds: turns state changes into events."""
        if not self.announced:
            self.announced = True
            self.sink.emit('service_started', f'pid {os.getpid()}, restarts {int(os.environ.get("MASA_RESTARTS", "0") or 0)}')
            for ident, job in list(self.console.jobs.items()):
                self.seen_jobs[ident] = job.get('status')
            if os.environ.get('MASA_AUTO_RESUME') == '1':
                self.auto_resume()
        for ident, job in list(self.console.jobs.items()):
            status = job.get('status')
            if self.seen_jobs.get(ident) != status:
                first = ident not in self.seen_jobs
                self.seen_jobs[ident] = status
                kind = JOB_EVENTS.get(status)
                if kind and not (first and status in TERMINAL):
                    self.sink.emit(kind[0], (job.get('note') or job.get('error') or job.get('phase') or '')[:200], job.get('run_id'), kind[1])
        current = {p['id']: p for p in self.pending()}
        for ident, item in current.items():
            if ident not in self.seen_pending:
                self.sink.emit('pending_added', f"{item['kind']}: {item['message']}", item.get('run_id'), 'warn')
        self.seen_pending = set(current)
        self._stalls()

    def _stalls(self):
        config = self.sink.config()
        limit = int(config['stall_minutes']) * 60
        if not self.worker_alive():
            return
        for ident, job in list(self.console.jobs.items()):
            rid = job.get('run_id')
            if job.get('status') != 'running' or not rid:
                continue
            store = Store(self.console.root)
            try:
                last = store.db.execute('SELECT MAX(created) FROM events WHERE run_id=?', (rid,)).fetchone()[0] or job.get('started') or self.clock()
            finally:
                store.close()
            last = max(last, (live.get(rid) or {}).get('updated') or 0)
            if self.clock() - last > limit and self.stalled.get(ident) != last:
                self.stalled[ident] = last
                minutes = round((self.clock() - last) / 60)
                self.sink.emit('run_stalled', f'no ledger activity for {minutes} minutes', rid, 'warn')
                if config['stall_action'] == 'stop':
                    self.console.control(rid, 'cancel')
                    self.sink.emit('run_stopped_stalled', 'cancelled by the stall rule', rid, 'warn')


class OpsMonitor(threading.Thread):
    def __init__(self, ops, interval=15):
        super().__init__(daemon=True, name='masa-ops-monitor')
        self.ops, self.interval, self.halt = ops, interval, threading.Event()

    def run(self):
        while not self.halt.is_set():
            try:
                self.ops.tick()
            except Exception:
                pass  # 监视失败不能影响任务 / a monitoring failure must never affect the work
            self.halt.wait(self.interval)
