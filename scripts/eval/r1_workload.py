"""R1：几小时的真实工作负载。先从零生成库存程序（bench/workload_stock.py 的 BASE_GOAL），再在每次通过的版本上逐个加 12 个功能（工作项），每一步之后用独立判官对已有的全部功能重判（回归也看得见）。
R1: a real multi-hour workload. Generate the stock program from scratch (BASE_GOAL in bench/workload_stock.py), then add 12 features one at a time on each passed version (work items); after every step the independent oracle
re-judges every feature so far (regressions show up too).

规则：某一步失败就从同一个基线重试一次；仍失败就停下——“第一个坏在哪里”正是 R1 要的答案。`--kill-at 步:秒` 在那一步跑到第几秒时用 os._exit 硬杀自己（相当于 kill -9），
之后用 `--resume` 重新启动：控制台把中断的任务标为 interrupted，这里恢复它，看系统能不能从账本接着做。
Rules: a failed step is retried once from the same baseline; a second failure stops the run - "where does it break first" is exactly R1's question. `--kill-at STEP:SECONDS` hard-kills this process with os._exit
(like kill -9) that many seconds into that step; restart with `--resume`: the console marks the interrupted job, it is resumed here, and we see whether the system continues from its ledger.

用法 / usage:
  python scripts/eval/r1_workload.py --name r1_flash --models flash --yuan 4 --per-step 0.6
  python scripts/eval/r1_workload.py --name r1_flash --models flash --resume
每一步一行追加到 .masa/<name>/r1.json（可续跑）。 Every step is appended to .masa/<name>/r1.json (resumable).
"""
import argparse
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts' / 'eval'))
from ab_eval import prepare, prepare_local  # noqa: E402
from masa.application.console import Console  # noqa: E402
from masa.application.usage import task_report  # noqa: E402
from masa.bench import oracle  # noqa: E402
from masa.bench import workload_stock as workload  # noqa: E402
from masa.bench.runner import SETTINGS_FILES, BenchRunner  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402
from masa.application.orchestration.routing import spend_from_report  # noqa: E402

POLL = 3


def load(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'steps': [], 'current': None, 'baseline': None, 'level': -1}


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    temp.replace(path)


def console_for(folder):
    root = folder / 'state'
    root.mkdir(parents=True, exist_ok=True)
    for name in SETTINGS_FILES:
        if (folder / name).exists():
            shutil.copy(folder / name, root / name)
    return Console(root, ROOT / '.tools/bin/masa-runner.exe', ROOT / '.tools/go/bin/go.exe', ROOT)


def wait(console, job_id, minutes, kill_after=None):
    """等任务结束；澄清问题选第一个选项；超过时限就取消。kill_after 秒后用 os._exit 硬杀本进程（模拟 kill -9）。
    Wait for the job; clarifying questions get the first option; cancel past the time limit. After kill_after seconds hard-kill this process with os._exit (simulating kill -9)."""
    started = time.time()
    while True:
        job = console.project_job(job_id)
        if kill_after is not None and time.time() - started >= kill_after:
            print(f'    !! kill -9 now (job {job_id[:8]}, phase {job.get("phase")})', flush=True)
            os._exit(137)
        if job['status'] == 'waiting_for_input':
            BenchRunner._answer_first(console, job)
        elif job['status'] != 'running':
            return job, round(time.time() - started, 1), False
        if time.time() - started > minutes * 60 and job.get('run_id'):
            console.control(job['run_id'], 'cancel')
            for _ in range(60):
                time.sleep(1)
                if console.project_job(job_id)['status'] != 'running':
                    break
            return console.project_job(job_id), round(time.time() - started, 1), True
        time.sleep(POLL)


def measure(console, job, prices):
    """一个任务的结果：通过了吗、最新的验证 run、这个任务自己的花费（整条链的花费减去开始前的）。 One job's outcome: passed?, the latest verification run, the job's own spend (the chain's minus what came before)."""
    store = Store(console.root)
    try:
        run_id = (job.get('result') or {}).get('id') or job.get('run_id')
        if not run_id:
            return {'outcome': 'no_run', 'note': str(job.get('error') or job.get('note') or '')[:300]}
        report = task_report(store, run_id)
        spend = spend_from_report(report, prices)
        offset = (job.get('routing') or {}).get('spend_offset') or {}
        own = {k: round(spend.get(k, 0) - offset.get(k, 0), 4) for k in ('cost', 'cloud_calls', 'local_calls', 'cloud_tokens', 'active_seconds')}
        latest = report['latest_run_id']
        files = None
        if report['outcome'] == 'succeeded':
            files = store.read(store.run(latest)['data']['project_bundle']['approval_ref'])['files']
        # 只数这个任务自己的版本（整条链是项目的全部历史）/ only this job's own versions (the chain is the project's whole history)
        mine = [v for v in report.get('versions') or [] if (v.get('created_at') or 0) >= (job.get('started') or 0) - 1]
        events = {}
        for version in mine:
            for event in store.events(version['run_id']):
                events[event['type']] = events.get(event['type'], 0) + 1
        verifications = sum(1 for v in mine if v['kind'] == 'verification')
        return {'outcome': report['outcome'], 'latest': latest, 'files': files, 'own': own, 'events': events, 'verifications': verifications,
                'note': str(job.get('note') or job.get('error') or '')[:300], 'status': job['status'], 'stop': ((report.get('routing') or {}).get('stopped') or {}).get('reason')}
    finally:
        store.close()


def judge(console, files, level):
    if not files:
        return None
    verdict = oracle.judge(files, workload.task_at(level), console._probe_executor(), 'python')
    owners = workload.feature_of_case(level)
    per = {}
    for case in verdict['cases']:
        feature = owners[case['index']]
        ok, total = per.get(feature, (0, 0))
        per[feature] = (ok + bool(case['ok']), total + 1)
    bad = [(owners[c['index']], c['reason'][:120]) for c in verdict['cases'] if not c['ok']]
    return {'passed': verdict['passed'], 'built': verdict.get('built'), 'error': verdict.get('error'), 'per_feature': {str(k): list(v) for k, v in per.items()}, 'failures': bad[:8]}


def shape(files, level, console):
    if not files:
        return {}
    code = [p for p in files if p.endswith('.py')]
    return {'files': len(files), 'py_files': len(code), 'loc': sum(files[p].count('\n') for p in code),
            'test_loc': sum(files[p].count('\n') for p in code if p.startswith('tests/')), 'level': level}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--models', default='flash')
    ap.add_argument('--local-ctx', type=int, default=16384)
    ap.add_argument('--yuan', type=float, default=4.0, help='整个负载的云端费用上限 / cloud cost cap of the whole workload')
    ap.add_argument('--per-step', type=float, default=0.6, help='单个任务（一步）的费用上限 / cost cap of one job (one step)')
    ap.add_argument('--max-calls', type=int, default=150)
    ap.add_argument('--step-minutes', type=int, default=45)
    ap.add_argument('--last', type=int, default=len(workload.CHANGES), help='做到第几个功能为止 / stop after this feature')
    ap.add_argument('--kill-at', default=None, help='STEP:SECONDS，在这一步跑到第几秒时硬杀本进程 / hard-kill this process that many seconds into that step')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--one-shot', action='store_true', help='对照：把基础需求和全部功能写成一个需求，一次生成，判官按最后一级判 / control: the base and every feature as ONE requirement, generated at once, judged at the last level')
    ap.add_argument('--source', type=Path, default=ROOT / '.masa/p2eval_final')
    ap.add_argument('--state', type=Path, default=ROOT / '.masa')
    args = ap.parse_args()
    folder = args.state / args.name
    progress = folder / 'r1.json'
    data = load(progress)
    if not progress.exists():
        if args.models.startswith('local:'):
            ids = prepare_local(args.source, folder, args.models[len('local:'):], args.local_ctx)
        else:
            ids = prepare(args.source, folder, args.models == 'flash+glm')
        data.update(model_ids=ids, models=args.models, started=time.time())
        save(progress, data)
    console = console_for(folder)
    prices = {}
    raw = json.loads((folder / 'provider.json').read_text(encoding='utf-8'))
    for profile in raw['profiles']:
        if profile.get('enabled') and profile.get('input_price_per_million') is not None:
            prices[profile['model']] = (profile['input_price_per_million'], profile['output_price_per_million'])
    kill_step, kill_seconds = (int(x) for x in args.kill_at.split(':')) if args.kill_at else (None, None)
    policy = {'start_level_by_role': {'project_repair': 2}} if not args.models.startswith('local:') else {'acceptance_cases': True}
    try:
        while data['level'] < args.last:
            spent = round(sum(s.get('own', {}).get('cost', 0) for s in data['steps']), 4)
            if spent >= args.yuan - 0.05:
                print(f'费用接近上限（{spent}/{args.yuan}），停止 / budget reached', flush=True)
                data['stopped'] = f'budget {spent}'
                break
            step = data['level'] + 1
            tries = sum(1 for s in data['steps'] if s['step'] == step)
            if tries >= 2:
                print(f'第 {step} 步两次都失败，停止 / step {step} failed twice, stopping', flush=True)
                data['stopped'] = f'step {step} failed twice'
                break
            budget = {'max_model_calls': args.max_calls, 'max_cost': round(min(args.per_step, args.yuan - spent), 4), 'max_active_seconds': args.step_minutes * 60,
                      'max_cloud_tokens': 3_000_000}
            current = data.get('current')
            if args.resume and current and current.get('step') == step:
                job_id = current['job_id']
                job = console.project_job(job_id)
                print(f"[{time.strftime('%H:%M:%S')}] 恢复第 {step} 步 / resume step {step}: job {job_id[:8]} status {job['status']} phase {job.get('phase')}", flush=True)
                if job['status'] == 'interrupted':
                    console.start_autonomous_project_job({}, resume_job=job_id)
                data.setdefault('resumed', []).append({'step': step, 'job_id': job_id, 'status_before': job['status'], 'phase': job.get('phase'), 'at': time.time()})
                args.resume = False
            else:
                body = {'routing': 'ladder', 'auto_verify': True, 'language': 'python', 'runtime': 'v2repair', 'budget': budget, 'policy': policy, 'model_ids': data['model_ids']}
                if step == 0:
                    goal = workload.BASE_GOAL
                    if args.one_shot:
                        goal += chr(10) * 2 + '另外还要支持下面这些功能（后面的优先）：' + chr(10) + chr(10).join(f'{i}. {c}' for i, c in enumerate(workload.CHANGES[:args.last], 1))
                    job_id = console.start_autonomous_project_job({**body, 'goal': goal})['job_id']
                else:
                    job_id = console.change_project(data['baseline'], {**body, 'change': workload.CHANGES[step - 1]})['job_id']
                data['current'] = {'step': step, 'job_id': job_id, 'baseline': data.get('baseline'), 'at': time.time()}
                save(progress, data)
                print(f"[{time.strftime('%H:%M:%S')}] 第 {step} 步开始 / step {step} started: job {job_id[:8]}" + (f" on {data['baseline'][:8]}" if step else ''), flush=True)
            job, seconds, timed_out = wait(console, job_id, args.step_minutes, kill_seconds if step == kill_step and not data.get('killed') else None)
            result = measure(console, job, prices)
            verdict = judge(console, result.get('files'), args.last if args.one_shot else step)
            record = {'step': step, 'job_id': job_id, 'seconds': seconds, 'timed_out': timed_out, 'outcome': result.get('outcome'), 'status': result.get('status'), 'stop': result.get('stop'),
                      'note': result.get('note'), 'own': result.get('own', {}), 'events': result.get('events', {}), 'verifications': result.get('verifications'), 'oracle': verdict, 'shape': shape(result.get('files'), step, console),
                      'latest': result.get('latest'), 'finished': time.time()}
            data['steps'].append(record)
            passed = result.get('outcome') == 'succeeded'
            if passed:
                data['level'], data['baseline'] = step, result['latest']
            if args.one_shot:
                data['level'] = args.last  # 一次就结束 / one job and done
            data['current'] = None
            save(progress, data)
            o = verdict or {}
            print(f"[{time.strftime('%H:%M:%S')}] 第 {step} 步 / step {step}: {result.get('outcome')} ({result.get('status')}) {seconds}s ¥{record['own'].get('cost')} 云调用 {record['own'].get('cloud_calls')} "
                  f"本地调用 {record['own'].get('local_calls')}  判官 {('通过' if o.get('passed') else '未通过 ' + json.dumps(o.get('per_feature'), ensure_ascii=False)) if verdict else '-'}  "
                  f"文件 {record['shape'].get('py_files')} 行 {record['shape'].get('loc')}  {result.get('note') or ''}", flush=True)
            if o.get('failures'):
                print('    判官失败 / oracle failures: ' + json.dumps(o['failures'], ensure_ascii=False)[:600], flush=True)
    finally:
        save(progress, data)
        try:
            console._release_local_models()
            console.close()
        except Exception:
            pass
    total = round(sum(s.get('own', {}).get('cost', 0) for s in data['steps']), 4)
    print(f"完成 / done: 做到第 {data['level']} 个功能，累计 ¥{total}，{len(data['steps'])} 个任务，用时 {round((time.time() - data['started']) / 60, 1)} 分钟", flush=True)
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
