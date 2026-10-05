"""恢复演练的子进程（不是测试，由 test_recovery_drill 启动并硬杀）。
Child process of the recovery drill (not a test): started and hard-killed by test_recovery_drill.

用法 / usage: python drill_child.py <state_dir> <start|resume> <none|verify|model|generation|cloudrepair>
  start  : 启动一个本地优先的自动任务。kill=verify 在第二次验证（本地修复后的验证）中卡住等待被杀；
           kill=model 在本地修复的模型调用中途卡住；kill=generation 在本地生成代码的模型调用中途卡住；kill=cloudrepair 在付费云修复的调用中途卡住。
  resume : 全新进程重开同一个状态目录并恢复被中断的任务。
所有“假模型调用/假工具执行”都追加到 <state>/drill-calls.log，供父进程跨进程计数。
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'src'))

from masa.application.console import Console  # noqa: E402
from masa.application.usage import task_report  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402
from test_project_generation import FILES  # noqa: E402
from test_project_plan import CHECKS, SPEC  # noqa: E402
from test_runtime import FakeExecutor  # noqa: E402

state, phase, kill = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
LOG = state / 'drill-calls.log'
BLOCKED = state / 'drill-blocked'


def log(line):
    with LOG.open('a', encoding='utf-8') as handle:
        handle.write(f'{os.getpid()} {line}\n')


def block(reason):
    """告诉父进程“我卡住了，可以杀我”，然后永远等待。 Tell the parent it may kill us, then wait forever."""
    BLOCKED.write_text(reason, encoding='utf-8')
    time.sleep(3600)


class Executor(FakeExecutor):
    """exit_code=1：验证总失败，直到云模型“修好”。 Verification fails until the cloud model 'fixes' it."""

    def execute(self, request, workspace, cancelled):
        if request['operation'] == 'go_test':
            log('verify')
            if kill == 'verify' and phase == 'start' and sum(1 for line in LOG.read_text().splitlines() if line.endswith(' verify')) == 2:
                block('verify#2')
        return super().execute(request, workspace, cancelled)


executor = Executor(exit_code=1)


class Model:
    def __init__(self, name):
        self.name = name
        self.profile = {'provider': 'test', 'model': name}
        self.config = {'model_type': 'local' if name == 'local' else 'cloud'}  # 账本层据此判断能否重试 / the ledger uses this to decide on retries
        self.usage = {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}

    def respond(self, context):
        purpose = context['purpose']
        log(f'{self.name} {purpose}')
        if kill == 'generation' and phase == 'start' and purpose == 'project_developer':
            block('local generation in flight')
        if purpose == 'project_repair':
            if self.name == 'cloud':
                if kill == 'cloudrepair' and phase == 'start':
                    block('paid cloud repair in flight')
                executor.exit_code = 0  # 云模型修好了 / the cloud model fixes it
            elif kill == 'model' and phase == 'start':
                block('local repair in flight')
            return {'internal/app/app.go': f'package app\n\nfunc Value() int {{ return 42 }} // {self.name}\n'}
        return {'project_triage': {'verdict': 'ok', 'reasons': [], 'suggestions': []}, 'project_planner': SPEC, 'project_tester': CHECKS, 'project_developer': FILES}[purpose]


MODELS = {'local': Model('local'), 'cloud': Model('cloud')}
for ident, model in MODELS.items():
    model.snapshot = {'version': 1, 'mode': 'fixed', 'profile_id': ident, 'config': {}}


def profile(level, kind, name):
    return {'level': level, 'priority': 0, 'model_type': kind, 'model': name, 'roles': [], 'context_limit': 32768,
            'max_output_tokens': 1024}


console = Console(state, Path('fake-runner'), Path('fake-go'), HERE.parent)
console.settings.ready_profiles = lambda: [('local', profile(1, 'local', 'local')), ('cloud', profile(2, 'cloud', 'cloud'))]
console.settings.provider = lambda ident=None, expected=None, snapshot=None: MODELS[ident or snapshot['profile_id']]
console._local_digests = lambda: None

# 真实的修复上下文需要 Go runner 建索引；演练里直接用完整文件。 The real context builder needs the Go runner; use whole files here.
with patch('masa.application.console.Runner', lambda *args: executor), \
        patch('masa.intelligence.repair_context.build_repair_context', lambda store, ex, rid, files, ev, fb: (files, None)):
    if phase == 'start':
        job_id = console.start_autonomous_project_job({'goal': 'Build a CLI', 'routing': 'ladder', 'policy': {'prefer_highest_roles': ['project_diagnoser', 'project_test_revision']}})['job_id']
        (state / 'drill-job.txt').write_text(job_id, encoding='utf-8')
    else:
        job_id = (state / 'drill-job.txt').read_text(encoding='utf-8')
        try:
            console.start_autonomous_project_job({}, resume_job=job_id)
        except Exception as exc:  # 恢复被明确拒绝 / resume explicitly refused
            print('RESULT ' + json.dumps({'refused': str(exc)}))
            sys.exit(0)
    console.job_thread.join()
    job = dict(console.jobs[job_id])

store = Store(state)
try:
    rid = (job.get('result') or {}).get('id') or job.get('run_id')
    report = task_report(store, rid)
    final = store.run(rid)['status']
finally:
    store.close()
print('RESULT ' + json.dumps({
    'status': job['status'], 'error': job.get('error'), 'note': job.get('note'), 'final_run_status': final,
    'route_history': job.get('route_history'), 'pending_fix': job.get('pending_fix'),
    'abandoned': sum(1 for c in report['calls'] if c['status'] == 'abandoned'),
    'escalations': (report['routing'] or {}).get('escalations'), 'models': {m['model']: m['calls'] for m in report['by_model']},
    'stopped': (report['routing'] or {}).get('stopped')}, ensure_ascii=False))
console.close()
