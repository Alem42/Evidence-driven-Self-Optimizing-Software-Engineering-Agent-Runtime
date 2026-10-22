"""对抗性探针：通过 Gate 之后，让没看过实现的角色去找缺陷，量出两种“独立证据”各能抓住多少真正的错误。零成本之外每个运行约 ¥0.03–0.05（Flash）。
Adversarial probe: after the Gate, roles that never saw the implementation look for defects; measures how many REAL wrong outputs each kind of "independent evidence" catches. About CNY 0.03-0.05 per run (Flash).

流程 / flow:
  1. qa_adversary：只凭目标和验收项提出 6–10 个新输入（不给期望）。 proposes new inputs from the requirement alone (no expectations).
  2. 实现在这些输入上真的跑一遍。 the implementation really runs on them.
  3. 检测器 A「盲推导审计」：另一个角色只看输入、独立推导结果，与实际输出比较（用例审计的同一套）。 detector A: blind derivation (the case-audit machinery) compared with the actual output.
  4. 检测器 B「参考实现差分」：reference_oracle 写一个最直白的暴力参考程序，在同样的输入上与实现比较。 detector B: a brute-force reference program written by reference_oracle, compared with the implementation on the same inputs.
  5. 事实标准：能在**任意输入**上判对错的任务（宽容判官或参考实现是通用的）；规格没规定的输入（非法/奇怪格式）没有事实标准，不计入。 ground truth only where the task can be judged on ANY input; inputs the requirement leaves open are not counted.

用法 / usage:  python scripts/eval/adversary_probe.py .masa/vb2_new --only v_cron,v_semver --one-per-task --max-yuan 0.8
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.application.checks.case_audit import audit_cases  # noqa: E402
from masa.bench import tasks_vague as v  # noqa: E402
from masa.bench.oracle import normalize  # noqa: E402
from masa.bench.runner import BY_ID  # noqa: E402
from masa.infrastructure.settings import Settings  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402

NO_WINDOW = {'creationflags': 0x08000000} if os.name == 'nt' else {}
PRICE = (1.0, 4.0)  # 元 / 百万 token（输入, 输出）/ CNY per million tokens
FLAGS = ['-E', '-s', '-B', '-X', 'utf8']
DATETIME = r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}'


def truth(task, args, stdin, out, err, code):
    """这个输出对不对（None = 这个任务没法对任意输入下结论）。 Is the output right (None = this task cannot be judged on arbitrary input)."""
    if task.id == 'v_semver' and not all(v.re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?', line) for line in stdin.split(chr(10)) if line):
        return None  # 非法/奇怪格式的版本号：规格没规定怎么办，没有事实标准 / invalid or oddly formatted versions: the requirement does not say, no ground truth
    if task.id in ('v_expenses', 'v_loglevels', 'v_deporder', 'v_semver'):
        return task.verify(args, stdin, out, err, code)[0]
    if task.id == 'v_cron':
        want, want_code = v.ref_cron(args, stdin)
        if want_code != 0:
            return code != 0 or 'error' in (out + err).lower() or '错误' in out + err
        return v.re.findall(DATETIME, out) == v.re.findall(DATETIME, want)
    if task.id == 'v_template':
        want, want_code = v.ref_template(args, stdin)
        if want_code != 0:
            return code != 0 or 'error' in (out + err).lower() or '错误' in out + err or '未定义' in out + err
        return ' '.join(out.split()) == ' '.join(want.split())
    if task.id in ('lcs', 'sched'):
        want, _ = task.ref(args, stdin)
        return normalize(out) == normalize(want)
    return None


def _run(command, directory, args, stdin, timeout):
    try:
        done = subprocess.run([sys.executable, *FLAGS, *command, *args], cwd=directory, input=stdin, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout, **NO_WINDOW)
    except subprocess.TimeoutExpired:
        return '', 'timeout', 124
    return done.stdout[:4000], done.stderr[:600], done.returncode


def run_program(files, module, args, stdin):
    with tempfile.TemporaryDirectory(prefix='adv-', ignore_cleanup_errors=True) as directory:
        for path, text in files.items():
            target = Path(directory) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8', newline='\n')
        return _run(['-m', module], directory, args, stdin, 20)


def run_script(source, args, stdin):
    with tempfile.TemporaryDirectory(prefix='ref-', ignore_cleanup_errors=True) as directory:
        (Path(directory) / 'reference.py').write_text(source, encoding='utf-8', newline='\n')
        return _run(['reference.py'], directory, args, stdin, 10)


def same_behaviour(a, b):
    """两个输出算不算同一种行为：标准输出（宽容空白）相同，且“成功/失败”相同。 Do two outputs count as the same behaviour: equal stdout (whitespace-tolerant) and the same success/failure."""
    return normalize(a[0]) == normalize(b[0]) and (a[2] == 0) == (b[2] == 0)


class Counting:
    """包一层提供方：记录 token 与花费，坏回答只算失败不中断。 Wrap a provider to count tokens and cost."""

    def __init__(self, provider):
        self.provider, self.prompt, self.completion = provider, 0, 0

    def respond(self, context):
        try:
            return self.provider.respond(context)
        finally:
            usage = getattr(self.provider, 'usage', None) or {}
            self.prompt += usage.get('prompt_tokens') or 0
            self.completion += usage.get('completion_tokens') or 0

    @property
    def yuan(self):
        return (self.prompt * PRICE[0] + self.completion * PRICE[1]) / 1e6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folders', nargs='+')
    ap.add_argument('--only', default='')
    ap.add_argument('--source', type=Path, default=ROOT / '.masa/p2eval_final')
    ap.add_argument('--max-yuan', type=float, default=1.0)
    ap.add_argument('--samples', type=int, default=3)
    ap.add_argument('--one-per-task', action='store_true', help='每个任务×臂只取第一次运行 / only the first run per task and arm')
    ap.add_argument('--out', type=Path, default=ROOT / '.masa' / 'adversary_probe.json')
    args = ap.parse_args()
    settings = Settings(args.source)
    ident = next(i for i, p in settings.ready_profiles() if p['model'] == 'deepseek-flash')
    provider = Counting(settings.provider(ident))
    wanted = set(filter(None, args.only.split(',')))
    seen, rows, spent = set(), [], 0.0
    for folder in args.folders:
        state = Path(folder)
        summary = json.loads((state / 'summary.json').read_text(encoding='utf-8'))
        for run in summary['runs']:
            if (wanted and run['task'] not in wanted) or (args.one_per_task and (state.name, run['task'], run['arm']) in seen):
                continue
            seen.add((state.name, run['task'], run['arm']))
            task = BY_ID[run['task']]
            store = Store(state / 'bench' / 'state' / run['bench_id'])
            try:
                finals = [r for r in store.all_runs() if r['data'].get('project_bundle')]
                if not finals:
                    continue
                bundle = store.read(finals[-1]['data']['project_bundle']['approval_ref'])
                approved = store.read(bundle['spec_approval_ref'])
            finally:
                store.close()
            if spent >= args.max_yuan:
                print(f'预算 {spent:.3f} 元已到，停止 / budget reached, stopping')
                break
            files, spec = bundle['files'], approved['spec']
            known = [c['input'] for check in approved.get('checks', []) for c in (check.get('cases') or [])]
            before = provider.yuan
            try:
                proposed = provider.respond({'purpose': 'qa_adversary', 'goal': task.goal, 'acceptance': spec['acceptance'], 'known_inputs': known[:16], 'language': 'python'})['inputs']
            except Exception as exc:
                print(f"{run['task']:12} {run['arm']:5} adversary failed: {str(exc)[:80]}")
                continue
            reference = None
            try:
                reference = provider.respond({'purpose': 'reference_oracle', 'goal': task.goal, 'acceptance': spec['acceptance'], 'language': 'python'})['lines']
            except Exception as exc:
                print(f"{run['task']:12} {run['arm']:5} reference failed: {str(exc)[:80]}")
            if isinstance(reference, list):
                reference = chr(10).join(str(line) for line in reference)
            cases, outputs, ref_runs = [], [], []
            for item in proposed:
                out, err, code = run_program(files, spec['module'], item['args'], item['stdin'])
                try:
                    right = truth(task, item['args'], item['stdin'], out, err, code)
                except Exception:  # 参考实现处理不了的输入（例如非法版本号）：没有事实标准 / an input the reference cannot handle: no ground truth
                    right = None
                outputs.append((item, out, err, code, right))
                ref_runs.append(run_script(reference, item['args'], item['stdin']) if reference else None)
                cases.append({'name': item['name'], 'input': 'args=' + json.dumps(item['args'], ensure_ascii=False) + '; stdin=' + json.dumps(item['stdin'], ensure_ascii=False),
                              'expected': 'stdout=' + json.dumps(out, ensure_ascii=False) + f'; exit code {code}'})

            def derive(views):
                answer = provider.respond({'purpose': 'case_deriver', 'goal': task.goal, 'acceptance': spec['acceptance'], 'cases': views, 'language': 'python'})
                return {a['index']: a['result'] for a in answer['answers']}

            def judge(pairs):
                answer = provider.respond({'purpose': 'case_judge', 'pairs': pairs, 'language': 'python'})
                return {a['index']: a['same'] for a in answer['answers']}
            status = audit_cases(cases, derive, judge, samples=args.samples)
            spent += provider.yuan - before
            judged = [i for i, o in enumerate(outputs) if o[4] is not None]
            wrong = [i for i in judged if outputs[i][4] is False]
            flagged = [i for i, s in status.items() if s == 'disagree' and i in judged]
            differs = [i for i in judged if ref_runs[i] is not None and not same_behaviour((outputs[i][1], outputs[i][2], outputs[i][3]), ref_runs[i])]
            row = {'folder': state.name, 'task': run['task'], 'arm': run['arm'], 'inputs': len(outputs), 'judgeable': len(judged), 'truly_wrong': len(wrong),
                   'audit_flagged': len(flagged), 'audit_tp': len(set(wrong) & set(flagged)), 'audit_fp': len(set(flagged) - set(wrong)),
                   'diff_flagged': len(differs), 'diff_tp': len(set(wrong) & set(differs)), 'diff_fp': len(set(differs) - set(wrong)),
                   'union_tp': len(set(wrong) & (set(flagged) | set(differs))), 'reference_ran': reference is not None, 'yuan': round(provider.yuan - before, 4),
                   'details': [{'name': outputs[i][0]['name'], 'why': outputs[i][0].get('why', ''), 'wrong': outputs[i][4], 'audit': status.get(i), 'differs': i in differs} for i in range(len(outputs))]}
            rows.append(row)
            print(f"{state.name:9} {run['task']:11} {run['arm']:5} judgeable {row['judgeable']:2} wrong {row['truly_wrong']}  | audit: flagged {row['audit_flagged']} caught {row['audit_tp']} false {row['audit_fp']}"
                  f"  | differential: flagged {row['diff_flagged']} caught {row['diff_tp']} false {row['diff_fp']}  | either caught {row['union_tp']}  ¥{row['yuan']:.4f} (累计 ¥{spent:.3f})", flush=True)
    total = {k: sum(r[k] for r in rows) for k in ('judgeable', 'truly_wrong', 'audit_flagged', 'audit_tp', 'audit_fp', 'diff_flagged', 'diff_tp', 'diff_fp', 'union_tp')}
    print()
    print(f"汇总 / total: 运行 {len(rows)}，可判对错的输入 {total['judgeable']}，真的错 {total['truly_wrong']}")
    print(f"  盲推导审计:   标出 {total['audit_flagged']}，命中 {total['audit_tp']}/{total['truly_wrong']}，误报 {total['audit_fp']}")
    print(f"  参考实现差分: 分歧 {total['diff_flagged']}，命中 {total['diff_tp']}/{total['truly_wrong']}，误报 {total['diff_fp']}")
    print(f"  两者任一:     命中 {total['union_tp']}/{total['truly_wrong']}；花费 ¥{spent:.3f}")
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
