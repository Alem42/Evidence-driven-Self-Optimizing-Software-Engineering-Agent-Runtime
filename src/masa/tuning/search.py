"""逐代淘汰搜索（successive halving）：每代让提议者给出若干补丁、逐个评测、淘汰较差的一半。预算耗尽立即停止并保存已有结果。
Successive-halving search: each generation the proposer yields patches, each is evaluated, and the worse half is dropped. Budget exhaustion stops at once and saves what exists.

防过拟合 / against overfitting:
  · 候选必须比当前最优**高出 min_gain 分**才会取代它（评测样本很小，噪声大，小幅领先不算数）。 A candidate must beat the best by min_gain points to replace it (tiny sample, noisy).
  · 报告里写明样本量；最终配置应该用更大的套餐复核一次（需要用户批准 token）。 The report states the sample size; the final config should be re-checked on a bigger suite (needs the user's token approval).
"""
import json
import math
import time
import uuid
from pathlib import Path

from masa.tuning import space
from masa.tuning.score import objective, summarize


def _entry(ident, generation, parent, patch, config, outcome, lam, mu):
    aggregate = outcome['aggregate']
    return {'id': ident, 'generation': generation, 'parent': parent, 'patch': patch, 'changes': space.describe(config), 'config': config,
            'score': objective(aggregate, lam=lam, mu=mu), 'summary': summarize(aggregate), 'result_id': outcome.get('result_id'),
            'cloud_tokens': aggregate['overall']['cloud_tokens']}


def search(evaluate, proposer, out_dir, *, width=4, generations=2, min_gain=2.0, max_candidates=8, total_cloud_tokens=400_000, total_minutes=120,
           lam=1.0, mu=1.0, clock=time.time, log=lambda text: None, tuning_id=None):
    """evaluate(config, cap_tokens) -> {'aggregate': ..., 'result_id': ...}。返回最终摘要，同时把每一代和最优配置写进 out_dir/<id>/。
    Returns the final summary and writes every generation and the best config under out_dir/<id>/."""
    ident = tuning_id or time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4]
    folder = Path(out_dir) / ident
    folder.mkdir(parents=True, exist_ok=True)
    started = clock()
    spent = {'cloud_tokens': 0, 'candidates': 0}
    state = {'id': ident, 'started': started, 'settings': {'width': width, 'generations': generations, 'min_gain': min_gain, 'max_candidates': max_candidates,
                                                             'total_cloud_tokens': total_cloud_tokens, 'total_minutes': total_minutes, 'lam': lam, 'mu': mu},
             'generations': [], 'discarded': [], 'stop_reason': None}

    def exhausted():
        if spent['candidates'] >= max_candidates:
            return f'候选数已达上限 {max_candidates}'
        if spent['cloud_tokens'] >= total_cloud_tokens:
            return f'云端 token 已达上限 {total_cloud_tokens}'
        if clock() - started >= total_minutes * 60:
            return f'时间已达上限 {total_minutes} 分钟'
        return None

    def evaluate_entry(generation, parent, patch, config):
        outcome = evaluate(config, max(1_000, total_cloud_tokens - spent['cloud_tokens']))
        spent['candidates'] += 1
        spent['cloud_tokens'] += outcome['aggregate']['overall']['cloud_tokens']
        return _entry(f'c{spent["candidates"]}', generation, parent, patch, config, outcome, lam, mu)

    def save(name, data):
        (folder / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    baseline = evaluate_entry(0, None, None, space.empty())
    log(f'基线 {baseline["id"]}: 得分 {baseline["score"]}')
    best, history, seen = baseline, [baseline], {space.fingerprint(baseline['config'])}
    entries = [baseline]
    survivors = [baseline]
    save('gen_00.json', {'generation': 0, 'candidates': [baseline]})
    state['generations'].append({'generation': 0, 'candidates': [baseline['id']], 'best': best['id']})
    for generation in range(1, generations + 1):
        state['stop_reason'] = exhausted()
        if state['stop_reason']:
            break
        children = []
        proposals = 0
        while len(children) < width and proposals < width * 4 and not exhausted():
            parent = survivors[proposals % len(survivors)]
            proposals += 1
            patch = proposer.propose(parent['config'], history)
            if patch is None:
                state['discarded'].append({'generation': generation, 'reason': 'no proposal'})
                continue
            try:
                config = space.apply(parent['config'], patch)
            except space.PatchError as exc:
                state['discarded'].append({'generation': generation, 'patch': patch, 'reason': str(exc)[:200]})  # 非法补丁：丢弃，不评测 / illegal patch: discarded, not evaluated
                continue
            if space.fingerprint(config) in seen:
                state['discarded'].append({'generation': generation, 'patch': patch, 'reason': 'duplicate configuration'})
                continue
            seen.add(space.fingerprint(config))
            child = evaluate_entry(generation, parent['id'], patch, config)
            log(f'第 {generation} 代 {child["id"]}: 得分 {child["score"]}  {"; ".join(child["changes"])}')
            children.append(child)
            history.append(child)
            entries.append(child)
            if child['score'] is not None and child['score'] >= (best['score'] if best['score'] is not None else -1e9) + min_gain:
                best = child
        pool = sorted(survivors + children, key=lambda c: (-(c['score'] if c['score'] is not None else -1e9), c['cloud_tokens']))
        survivors = pool[:max(1, math.ceil(len(pool) / 2))]  # 淘汰较差的一半 / drop the worse half
        if best not in survivors:
            survivors.append(best)
        save(f'gen_{generation:02d}.json', {'generation': generation, 'candidates': children, 'survivors': [s['id'] for s in survivors], 'best': best['id']})
        state['generations'].append({'generation': generation, 'candidates': [c['id'] for c in children], 'survivors': [s['id'] for s in survivors], 'best': best['id']})
        if not children:
            state['stop_reason'] = state['stop_reason'] or exhausted() or '这一代没有可评测的合法补丁'
            break
    state['stop_reason'] = state['stop_reason'] or exhausted() or '搜索完成'
    final = _finish(folder, state, baseline, best, spent, clock() - started, min_gain, entries)
    return final


def _finish(folder, state, baseline, best, spent, seconds, min_gain, entries):
    improved = best is not baseline
    overrides = space.materialize(best['config'])
    result = {
        'id': state['id'], 'verdict': 'improved' if improved else 'no_improvement', 'min_gain': min_gain,
        'baseline': {'id': baseline['id'], 'score': baseline['score'], 'summary': baseline['summary']},
        'best': {'id': best['id'], 'score': best['score'], 'summary': best['summary'], 'changes': best['changes'], 'config': best['config'], 'overrides': overrides},
        'gain': None if baseline['score'] is None or best['score'] is None else round(best['score'] - baseline['score'], 2),
        'spent': {**spent, 'seconds': round(seconds, 1)}, 'stop_reason': state['stop_reason'], 'discarded': len(state['discarded']),
        'sample': {'runs_per_candidate': baseline['summary']['runs']},
    }
    (folder / 'best.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    (folder / 'state.json').write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
    (folder / 'report.md').write_text(report(result, entries), encoding='utf-8')
    return result


def report(result, entries):
    """人读的报告：相对基线改了什么、得分变化、样本量与限制。不夸大。 The human report: what changed against the baseline, the score change, sample size and caveats. No exaggeration."""
    b, w = result['baseline'], result['best']
    lines = [f"# 调优报告 {result['id']}", '',
             f"- 结论：{'找到了高于基线 ' + str(result['min_gain']) + ' 分以上的配置' if result['verdict'] == 'improved' else '没有任何候选比基线高出 ' + str(result['min_gain']) + ' 分，保持现有设置'}",
             f"- 基线得分 {b['score']} → 最优 {w['score']}（差 {result['gain']}）", f"- 停止原因：{result['stop_reason']}",
             f"- 花费：{result['spent']['candidates']} 个候选、云端 {result['spent']['cloud_tokens']} token、{result['spent']['seconds']} 秒；丢弃的不合法/重复补丁 {result['discarded']} 个",
             f"- **样本量**：每个候选 {result['sample']['runs_per_candidate']} 次运行。样本很小，得分差可能是噪声；采用前请用更大的套餐复核。", '',
             '## 相对基线的改动', *[f'- {line}' for line in w['changes']], '', '## 各代候选', '| 代 | 候选 | 得分 | 通过率 | 云端 token | 改动 |', '|---|---|---|---|---|---|']
    rows = [f"| {e['generation']} | {e['id']} | {e['score']} | {e['summary']['pass_rate']} | {e['cloud_tokens']} | {'; '.join(e['changes'])} |" for e in entries]
    return '\n'.join(lines + rows) + '\n'
