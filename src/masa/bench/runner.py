"""评测运行器：按套餐顺序跑任务，严格限制 token 与时间，记录每次运行的指标，并用独立判官复核。
Benchmark runner: run tasks in order under hard token and time limits, record per-run metrics, and re-check with the independent oracle.

限制分三层，任何一层到顶都会停下并保留证据：
Three layers of limits; any of them stops the work and keeps the evidence:
1. 单次运行：路由层的任务预算（云端 token / 活跃秒数 / 调用数），外加一条硬性墙钟线（到点就取消）。
   Per run: the routing task budget (cloud tokens / active seconds / calls) plus a hard wall-clock line (cancel at the deadline).
2. 整个评测：云端 token 总量与总分钟数；到顶后剩下的任务标为“跳过”。
   Whole suite: total cloud tokens and minutes; once reached, remaining runs are marked skipped.
3. 用户随时点“停止”：取消当前运行，剩下的标为跳过。
   The user may stop at any time: cancel the current run and skip the rest.
"""
import json
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from masa.bench import oracle
from masa.bench.report import ERROR, FALSE_PASS, GATE_FAIL, PASS, SKIPPED, STOPPED, TIMEOUT, aggregate
from masa.bench.tasks import BY_ID, SUITES, TASKS
from masa.infrastructure.proc import NO_WINDOW

SETTINGS_FILES = ('provider.json', 'provider-keys.local.json', 'routing.json', 'token-calibration.json')
POLL_SECONDS = 2


def resolve_config(body):
    """把请求变成确定的配置（套餐默认值 + 用户覆盖），并校验范围。 Turn a request into a concrete, validated config."""
    from masa.domain.models import MasaError
    body = body or {}
    suite = body.get('suite') or 'canary'
    if suite != 'custom' and suite not in SUITES:
        raise MasaError('unknown benchmark suite')
    preset = SUITES.get(suite, {'tasks': [], 'repeats': 1, 'total_cloud_tokens': 120_000, 'total_minutes': 30})
    task_ids = body.get('task_ids') or preset['tasks']
    if not isinstance(task_ids, list) or not task_ids or any(t not in BY_ID for t in task_ids):
        raise MasaError('unknown or empty task list')

    def bounded(name, default, low, high):
        value = body.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise MasaError(f'{name} must be an integer in {low}..{high}')
        return value

    return {
        'suite': suite, 'task_ids': list(dict.fromkeys(task_ids)),
        'repeats': bounded('repeats', preset['repeats'], 1, 10),
        'total_cloud_tokens': bounded('total_cloud_tokens', preset['total_cloud_tokens'], 1_000, 50_000_000),
        'total_minutes': bounded('total_minutes', preset['total_minutes'], 1, 2_000),
        # 单次运行的上限：缺省按任务等级给；这里的数字是“倍率”，100 = 默认。 Per-run limits default by level; the number is a percentage, 100 = default.
        'per_run_scale': bounded('per_run_scale', 100, 20, 400),
        'model_ids': body.get('model_ids') if isinstance(body.get('model_ids'), list) else None,
    }


def count_mechanisms(report, store, run_ids):
    """从账本事件里数出各个 runtime 机制被触发了几次——这是“改动有没有起作用”的直接证据。
    Count how often each runtime mechanism fired, straight from ledger events: direct evidence that a change is in effect."""
    counts = {'imports_fixed': 0, 'syntax_rewrites': 0, 'diagnosis': 0, 'reconciled': 0, 'rewrite': 0, 'rounds_extended': 0, 'noop': 0, 'flip_to_tests': 0}
    for item in report.get('process') or []:
        kind, data = item['kind'], item['data']
        if kind == 'imports_fixed':
            counts['imports_fixed'] += 1
        elif kind == 'diagnosis':
            counts['diagnosis'] += 1
            counts['reconciled'] += bool(data.get('reconciled_from'))
        elif kind == 'rewrite_started':
            counts['rewrite'] += 1
        elif kind == 'rounds_extended':
            counts['rounds_extended'] += 1
        elif kind == 'noop_revision':
            counts['noop'] += 1
        elif kind == 'workflow_node' and data.get('why') == 'flip_to_tests':
            counts['flip_to_tests'] += 1
    from masa.runtime.roles import RoleRuntime
    roles = RoleRuntime(store)
    for run_id in run_ids:
        for call in roles.states(run_id):
            counts['syntax_rewrites'] += ':syntax' in str(call.get('invocation_id', ''))
    return counts


class BenchRunner:
    """一次评测的执行者。可注入 run_task 做离线测试（不调用任何模型）。
    Executes one benchmark; run_task can be injected for offline tests (no model is called)."""

    def __init__(self, main_root, runner_path, go_path, project, config, *, run_task=None, clock=time.time, on_change=None):
        self.main_root = Path(main_root)
        self.runner_path, self.go_path, self.project = Path(runner_path), Path(go_path), Path(project)
        self.config = config
        self.clock = clock
        self.on_change = on_change or (lambda state: None)
        self.run_task = run_task or self._run_task_for_real
        self.id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4]
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.records = []
        self.state = {'id': self.id, 'state': 'starting', 'config': config, 'started': clock(), 'current': None, 'used': {'cloud_tokens': 0, 'seconds': 0},
                      'total': len(config['task_ids']) * config['repeats'], 'message': None}
        self.console = None
        self.scratch = self.main_root / 'bench' / 'state' / self.id

    # ───────────── 外部接口 / public ─────────────
    def snapshot(self):
        with self.lock:
            return {**self.state, 'records': list(self.records), 'aggregate': aggregate(self.records)}

    def stop(self):
        self.stop_flag.set()

    def run(self):
        """顺序跑完整个评测；无论怎样结束都会保存结果并释放本地模型。 Run the whole suite; always persists results and releases local models."""
        try:
            self._set(state='running')
            plan = [(BY_ID[t], i) for i in range(self.config['repeats']) for t in self.config['task_ids']]
            plan.sort(key=lambda item: (item[1], item[0].level))  # 先一轮、等级从低到高：预算不够时先保住低等级的数据 / low levels first when budget runs out
            for index, (task, repeat) in enumerate(plan):
                reason = self._cap_reason()
                if reason or self.stop_flag.is_set():
                    self._add(self._skipped(task, repeat, reason or '用户停止'))
                    continue
                self._set(current={'task': task.id, 'level': task.level, 'repeat': repeat + 1, 'index': index + 1, 'started': self.clock()})
                record = self._guarded(task, repeat)
                self._add(record)
                self._save()
            self._set(state='stopped' if self.stop_flag.is_set() else 'done', current=None)
        except Exception as exc:  # 评测本身出错不能吞掉已有数据 / a harness failure must not lose collected data
            self._set(state='error', message=f'{type(exc).__name__}: {str(exc)[:200]}', current=None)
        finally:
            self._save()
            self._cleanup()

    # ───────────── 内部 / internals ─────────────
    def _set(self, **fields):
        with self.lock:
            self.state.update(fields)
        self.on_change(self.state)

    def _add(self, record):
        with self.lock:
            self.records.append(record)
            self.state['used'] = {'cloud_tokens': sum(r.get('cloud_tokens') or 0 for r in self.records), 'seconds': round(self.clock() - self.state['started'], 1)}
        self.on_change(self.state)

    def _cap_reason(self):
        used = self.state['used']['cloud_tokens']
        if used >= self.config['total_cloud_tokens']:
            return f"评测云端 token 总量已达上限（{used}/{self.config['total_cloud_tokens']}）"
        if self.clock() - self.state['started'] >= self.config['total_minutes'] * 60:
            return f"评测总时间已达上限（{self.config['total_minutes']} 分钟）"
        return None

    def _limits(self, task):
        scale = self.config['per_run_scale'] / 100
        remaining_tokens = max(1_000, self.config['total_cloud_tokens'] - self.state['used']['cloud_tokens'])
        remaining_seconds = max(30, self.config['total_minutes'] * 60 - (self.clock() - self.state['started']))
        return {'cloud_tokens': int(min(task.max_cloud_tokens * scale, remaining_tokens)), 'seconds': int(min(task.max_seconds * scale, remaining_seconds))}

    def _skipped(self, task, repeat, reason):
        return self._blank(task, repeat, SKIPPED, note=reason)

    @staticmethod
    def _blank(task, repeat, status, **extra):
        return {'task': task.id, 'level': task.level, 'repeat': repeat + 1, 'status': status, 'gate_passed': False, 'oracle_passed': None, 'oracle_reason': '',
                'seconds': 0, 'calls': 0, 'failed_calls': 0, 'local_tokens': 0, 'cloud_tokens': 0, 'escalations': 0, 'rounds': 0, 'mechanisms': {}, 'stop_reason': None,
                'note': None, 'run_id': None, 'fail_stage': None, **extra}

    def _guarded(self, task, repeat):
        started = self.clock()
        try:
            record = self.run_task(task, repeat, self._limits(task))
        except Exception as exc:
            record = self._blank(task, repeat, ERROR, note=f'{type(exc).__name__}: {str(exc)[:200]}')
        record['wall_seconds'] = round(self.clock() - started, 1)
        return record

    def _save(self):
        folder = self.main_root / 'bench' / 'results'
        folder.mkdir(parents=True, exist_ok=True)
        snap = self.snapshot()
        snap.update(commit=_commit(self.project), models=self._models(), finished=self.clock() if snap['state'] != 'running' else None)
        temp = folder / f'{self.id}.tmp'
        temp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding='utf-8')
        temp.replace(folder / f'{self.id}.json')

    def _models(self):
        try:
            return [f"L{p['level']} {p['model']}" for _, p in (self.console.settings.ready_profiles() if self.console else [])]
        except Exception:
            return []

    def _cleanup(self):
        if self.console is not None:
            try:
                self.console._release_local_models()
                self.console.close()
            except Exception:
                pass
        shutil.rmtree(self.scratch, ignore_errors=True)

    # ───────────── 真实运行一个任务 / run one task for real ─────────────
    def _scratch_console(self):
        from masa.application.console import Console
        self.scratch.mkdir(parents=True, exist_ok=True)
        for name in SETTINGS_FILES:
            source = self.main_root / name
            if source.exists():
                shutil.copy(source, self.scratch / name)
        if self.config.get('model_ids'):
            settings = self.scratch / 'provider.json'
            raw = json.loads(settings.read_text(encoding='utf-8'))
            for profile in raw['profiles']:
                profile['enabled'] = profile['id'] in set(self.config['model_ids'])
            settings.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        self.console = Console(self.scratch, self.runner_path, self.go_path, self.project)
        return self.console

    def _run_task_for_real(self, task, repeat, limits):
        from masa.application.usage import task_report
        from masa.infrastructure.store import Store
        console = self.console or self._scratch_console()
        record = self._blank(task, repeat, GATE_FAIL)
        started = self.clock()
        body = {'goal': task.goal, 'routing': 'ladder', 'auto_verify': True, 'api_profile_id': next(iter(console.settings.profiles), None),
                'budget': {'max_cloud_tokens': limits['cloud_tokens'], 'max_active_seconds': limits['seconds'], 'max_model_calls': 40}}
        job_id = console.start_autonomous_project_job(body)['job_id']
        deadline = started + limits['seconds'] + 30  # 路由层按“活跃秒数”停，这里是不依赖它的硬性墙钟线 / hard wall clock independent of the routing budget
        timed_out = False
        while True:
            job = console.project_job(job_id)
            if job['status'] == 'waiting_for_input':
                self._answer_first(console, job)
            elif job['status'] != 'running':
                break
            if (self.clock() > deadline or self.stop_flag.is_set()) and job.get('run_id'):
                timed_out = self.clock() > deadline
                console.control(job['run_id'], 'cancel')
                for _ in range(30):
                    time.sleep(1)
                    if console.project_job(job_id)['status'] != 'running':
                        break
                break
            time.sleep(POLL_SECONDS)
        job = console.project_job(job_id)
        record['seconds'] = round(self.clock() - started, 1)
        console._release_local_models()
        store = Store(console.root)
        try:
            run_id = (job.get('result') or {}).get('id') or job.get('run_id')
            record['run_id'] = run_id
            if not run_id:
                return {**record, 'status': ERROR, 'note': str(job.get('error') or 'no run was created')[:200]}
            report = task_report(store, run_id)
            totals = report['totals']
            routing = report.get('routing') or {}
            versions = report.get('versions') or []
            record.update(calls=totals['calls'], failed_calls=totals['failed_calls'], local_tokens=totals['local_tokens'], cloud_tokens=totals['cloud_tokens'],
                          escalations=routing.get('escalations', 0), rounds=sum(1 for v in versions if v['kind'] == 'verification'),
                          mechanisms=count_mechanisms(report, store, [v['run_id'] for v in versions]),
                          stop_reason=(routing.get('stopped') or {}).get('reason'))
            if timed_out:
                return {**record, 'status': TIMEOUT, 'note': '到达单次运行的时间上限，已取消'}
            if report['outcome'] == 'succeeded':
                record['gate_passed'] = True
                files = store.read(store.run(report['latest_run_id'])['data']['project_bundle']['approval_ref'])['files']
                verdict = oracle.judge(files, task, console.go_path)
                bad = [c for c in verdict['cases'] if not c['ok']]
                record.update(oracle_passed=verdict['passed'], oracle_reason=(verdict.get('error') or (bad[0]['reason'] if bad else ''))[:200])
                record['status'] = PASS if verdict['passed'] else FALSE_PASS
                return record
            record['fail_stage'] = 'generation' if job['status'] == 'failed' else 'verification'
            if job['status'] == 'failed':
                record['note'] = str(job.get('error') or '')[:200]
            record['status'] = STOPPED if record['stop_reason'] else GATE_FAIL
            return record
        finally:
            store.close()

    @staticmethod
    def _answer_first(console, job):
        """规划阶段的澄清问题：评测里一律选第一个选项，保证无人值守。 Clarifying questions are answered with the first option so runs are unattended."""
        from masa.infrastructure.store import Store
        store = Store(console.root)
        try:
            plan = store.run(job['run_id'])['data']['project_plan']
        finally:
            store.close()
        answers = {q['key']: {'option_id': q['options'][0]['id']} for q in plan['clarification']['questions']}
        console.answer_clarification(job['run_id'], {'question_id': plan['clarification_id'], 'answers': answers})
        time.sleep(1)


def _commit(project):
    """当前代码版本（评测结果要能对应到 runtime 的某一版）。 The code version this result belongs to."""
    try:
        head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=project, capture_output=True, timeout=5, **NO_WINDOW).stdout.decode().strip()
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=project, capture_output=True, timeout=5, **NO_WINDOW).stdout.strip()
        return head + ('+dirty' if dirty else '') if head else None
    except Exception:
        return None


def list_results(main_root):
    """已保存的评测摘要，新的在前。 Saved results, newest first."""
    folder = Path(main_root) / 'bench' / 'results'
    out = []
    for path in sorted(folder.glob('*.json'), reverse=True) if folder.exists() else []:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        agg = data.get('aggregate') or aggregate(data.get('records', []))
        out.append({'id': data['id'], 'suite': data['config']['suite'], 'state': data['state'], 'started': data['started'], 'commit': data.get('commit'),
                    'models': data.get('models', []), **{k: agg['overall'][k] for k in ('runs', 'passed', 'rate', 'false_pass', 'stable_level', 'ceiling_level', 'weighted_score', 'cloud_tokens', 'seconds')}})
    return out


def load_result(main_root, result_id):
    from masa.domain.models import MasaError
    if not isinstance(result_id, str) or not result_id.replace('-', '').isalnum():
        raise MasaError('invalid result id')
    path = Path(main_root) / 'bench' / 'results' / f'{result_id}.json'
    if not path.is_file():
        raise MasaError('benchmark result not found')
    return json.loads(path.read_text(encoding='utf-8'))


__all__ = ['BenchRunner', 'resolve_config', 'list_results', 'load_result', 'TASKS']
