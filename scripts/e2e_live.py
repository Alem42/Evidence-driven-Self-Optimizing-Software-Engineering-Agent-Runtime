"""真实端到端：用指定模型跑完整自动流程，并打印每次调用的用量与失败证据。会调用真实模型（可能产生费用）。
Real end-to-end driver: runs the full automatic workflow with a chosen profile and prints per-call usage
and failure evidence. Uses a COPY of the settings in a scratch state dir; never touches .masa history."""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from masa.application.console import Console  # noqa: E402
from masa.application.usage import task_report  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--goal', required=True)
    ap.add_argument('--profile', default='legacy', help='profile id (default: legacy = DeepSeek)')
    ap.add_argument('--state', type=Path, required=True, help='scratch state dir (settings are copied into it)')
    ap.add_argument('--answer', default='first', choices=['first', 'none'], help='auto-answer clarification with first option')
    ap.add_argument('--timeout', type=int, default=1500)
    ap.add_argument('--routing', default='fixed', choices=['fixed', 'ladder'], help='ladder = local first, bounded escalation, task budget')
    ap.add_argument('--max-cloud-tokens', type=int, default=None)
    ap.add_argument('--keep', default=None, help='comma-separated profile ids to keep (others are disabled in the scratch copy)')
    args = ap.parse_args()
    args.state.mkdir(parents=True, exist_ok=True)
    for name in ('provider.json', 'provider-keys.local.json'):
        if (ROOT / '.masa' / name).exists() and not (args.state / name).exists():
            shutil.copy(ROOT / '.masa' / name, args.state / name)
    if args.keep:
        # 只在临时拷贝里禁用其它配置，保证候选集合可控。 Disable the other profiles in the scratch copy only.
        settings_file = args.state / 'provider.json'
        raw = json.loads(settings_file.read_text(encoding='utf-8'))
        keep = set(args.keep.split(','))
        for p in raw['profiles']:
            p['enabled'] = p['id'] in keep
        settings_file.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    console = Console(args.state, ROOT / '.tools/bin/masa-runner.exe', ROOT / '.tools/go/bin/go.exe', ROOT)
    body = {'goal': args.goal, 'api_profile_id': args.profile}
    if args.routing == 'ladder':
        body['routing'] = 'ladder'
        if args.max_cloud_tokens:
            body['budget'] = {'max_cloud_tokens': args.max_cloud_tokens}
    job_id = console.start_autonomous_project_job(body)['job_id']
    print('JOB', job_id, flush=True)
    deadline = time.time() + args.timeout
    last = None
    while time.time() < deadline:
        job = console.project_job(job_id)
        state = (job['status'], job.get('stage'), job.get('run_id'))
        if state != last:
            print(time.strftime('%H:%M:%S'), *state, flush=True)
            last = state
        if job['status'] == 'waiting_for_input' and args.answer == 'first':
            store = Store(console.root)
            try:
                plan = store.run(job['run_id'])['data']['project_plan']
            finally:
                store.close()
            answers = {q['key']: {'option_id': q['options'][0]['id']} for q in plan['clarification']['questions']}
            print('  auto-answer:', json.dumps(answers, ensure_ascii=False), flush=True)
            console.answer_clarification(job['run_id'], {'question_id': plan['clarification_id'], 'answers': answers})
            time.sleep(1)
            continue
        if job['status'] != 'running':
            break
        time.sleep(2)
    job = console.project_job(job_id)
    store = Store(console.root)
    try:
        rid = job.get('result', {}).get('id') or job.get('run_id')
        report = task_report(store, rid)
    finally:
        store.close()
    print('\nFINAL job:', job['status'], '| outcome:', report['outcome'], '| note:', job.get('note'), '| error:', job.get('error'))
    t = report['totals']
    print('tokens total', t['total_tokens'], 'local', t['local_tokens'], 'cloud', t['cloud_tokens'], 'calls', t['calls'], 'failed', t['failed_calls'], 'wall', report['wall_seconds'], 's')
    for c in report['calls']:
        print(f"  {c['step_id']:<22}{str(c['invocation_id'])[:10]:<12}{c['status']:<10}{c['model']:<22}tok={c['total_tokens']} {c['duration_ms']}ms {c['error'] or ''}")
    routing = report.get('routing')
    if routing:
        print('ROUTING', routing['mode'], 'escalations', routing['escalations'], 'spend', routing['spend'], 'stopped', routing['stopped'])
        for d in routing['decisions']:
            print(f"  {d['chain']:<11}{d['role']:<22}{d['action']:<5}{str(d['model']):<22}L{d['level']} {d['reason']}{' ESCALATED' if d['escalated'] else ''}")
    print('RUN', rid)
    console.close()
    return 0 if report['outcome'] == 'succeeded' else 1


if __name__ == '__main__':
    raise SystemExit(main())
