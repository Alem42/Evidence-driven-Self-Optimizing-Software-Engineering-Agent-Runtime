"""评测结果的汇总与对比：按等级、按任务、综合评分，以及两次评测之间的差异。纯函数，无 I/O。
Aggregation and comparison of benchmark results: per level, per task, overall scores, and the difference between two runs. Pure functions.
"""

# 一次运行的结局 / run outcomes
PASS, FALSE_PASS, GATE_FAIL, STOPPED, TIMEOUT, ERROR, SKIPPED = 'pass', 'false_pass', 'gate_fail', 'stopped', 'timeout', 'error', 'skipped'
STATUS_TEXT = {PASS: '通过', FALSE_PASS: '假通过', GATE_FAIL: '未通过', STOPPED: '预算/路由停止', TIMEOUT: '超时', ERROR: '出错', SKIPPED: '跳过（超出评测上限）'}
MECHANISMS = ('imports_fixed', 'syntax_rewrites', 'diagnosis', 'reconciled', 'rewrite', 'rounds_extended', 'noop', 'flip_to_tests',
              'conductor', 'conductor_rejected', 'skeptic', 'code_review')


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def aggregate(records):
    """records：每次运行一条（见 runner.py）。返回 {overall, by_level, by_task}。 One record per run (see runner.py)."""
    attempted = [r for r in records if r['status'] != SKIPPED]
    by_level, by_task = {}, {}
    for record in attempted:
        for key, bucket in ((str(record['level']), by_level), (record['task'], by_task)):
            row = bucket.setdefault(key, {'runs': 0, 'passed': 0, 'false_pass': 0, 'cloud': [], 'local': [], 'seconds': [], 'calls': [], 'rounds': [], 'escalations': [],
                                          'level': record['level'], 'outcomes': {}})
            row['runs'] += 1
            row['passed'] += record['status'] == PASS
            row['false_pass'] += record['status'] == FALSE_PASS
            row['outcomes'][record['status']] = row['outcomes'].get(record['status'], 0) + 1
            for field, values in (('cloud_tokens', 'cloud'), ('local_tokens', 'local'), ('seconds', 'seconds'), ('calls', 'calls'), ('rounds', 'rounds'), ('escalations', 'escalations')):
                row[values].append(record.get(field))
    for bucket in (by_level, by_task):
        for row in bucket.values():
            row['rate'] = round(row['passed'] / row['runs'], 3)
            for source, target in (('cloud', 'mean_cloud_tokens'), ('local', 'mean_local_tokens'), ('seconds', 'mean_seconds'), ('calls', 'mean_calls'),
                                   ('rounds', 'mean_rounds'), ('escalations', 'mean_escalations')):
                row[target] = _mean(row.pop(source))
    levels_with_runs = sorted(int(k) for k in by_level)
    stable = max((lv for lv in levels_with_runs if by_level[str(lv)]['rate'] >= 0.67), default=None)
    ceiling = max((lv for lv in levels_with_runs if by_level[str(lv)]['passed'] > 0), default=None)
    weight = lambda lv: lv + 1  # noqa: E731  高等级的通过更值钱 / a pass at a higher level is worth more
    total_w = sum(weight(r['level']) for r in attempted)
    got_w = sum(weight(r['level']) for r in attempted if r['status'] == PASS)
    passed = [r for r in attempted if r['status'] == PASS]
    mechanisms = {m: sum(r.get('mechanisms', {}).get(m, 0) for r in attempted) for m in MECHANISMS}
    return {
        'overall': {
            'runs': len(attempted), 'skipped': len(records) - len(attempted), 'passed': len(passed), 'rate': round(len(passed) / len(attempted), 3) if attempted else None,
            'false_pass': sum(r['status'] == FALSE_PASS for r in attempted),
            'stable_level': stable, 'ceiling_level': ceiling, 'weighted_score': round(100 * got_w / total_w, 1) if total_w else None,
            'cloud_tokens': sum(r.get('cloud_tokens') or 0 for r in attempted), 'local_tokens': sum(r.get('local_tokens') or 0 for r in attempted),
            'seconds': round(sum(r.get('seconds') or 0 for r in attempted), 1),
            'cloud_tokens_per_pass': round(sum(r.get('cloud_tokens') or 0 for r in attempted) / len(passed)) if passed else None,
            'seconds_per_pass': round(sum(r.get('seconds') or 0 for r in attempted) / len(passed), 1) if passed else None,
            'mechanisms': mechanisms,
        },
        'by_level': by_level, 'by_task': by_task,
    }


def compare(base, new):
    """两次评测的差异（new - base）。只比较两边都有数据的等级。 Differences (new - base) over levels present in both."""
    out = {'levels': {}, 'overall': {}}
    for key, label in (('rate', 'rate'), ('weighted_score', 'weighted_score'), ('cloud_tokens_per_pass', 'cloud_tokens_per_pass'), ('seconds_per_pass', 'seconds_per_pass'),
                       ('stable_level', 'stable_level'), ('ceiling_level', 'ceiling_level'), ('false_pass', 'false_pass')):
        a, b = base['overall'].get(key), new['overall'].get(key)
        out['overall'][label] = {'base': a, 'new': b, 'delta': round(b - a, 3) if a is not None and b is not None else None}
    for level in sorted(set(base['by_level']) & set(new['by_level']), key=int):
        a, b = base['by_level'][level], new['by_level'][level]
        out['levels'][level] = {'base_rate': a['rate'], 'new_rate': b['rate'], 'delta': round(b['rate'] - a['rate'], 3),
                                'base_cloud': a['mean_cloud_tokens'], 'new_cloud': b['mean_cloud_tokens'], 'base_seconds': a['mean_seconds'], 'new_seconds': b['mean_seconds']}
    return out


def format_table(result):
    """文本表格（命令行用）。 A plain-text table for the command line."""
    agg = result.get('aggregate') or aggregate(result['records'])
    o = agg['overall']
    lines = [f"评测 {result.get('id', '')}  套餐={result.get('config', {}).get('suite')}  提交={result.get('commit')}  模型={', '.join(result.get('models', []))}",
             f"通过 {o['passed']}/{o['runs']}（{'—' if o['rate'] is None else format(o['rate'] * 100, '.0f') + '%'}） · 假通过 {o['false_pass']} · 稳定等级 L{o['stable_level']} · 最高通过等级 L{o['ceiling_level']}"
             f" · 加权得分 {o['weighted_score']} · 云端 {o['cloud_tokens']} tok · 本地 {o['local_tokens']} tok · {o['seconds']} 秒"
             if o['runs'] else '（没有完成的运行）', '', '等级   运行  通过率  云端tok(均)  秒(均)  调用(均)  验证轮(均)']
    for level in sorted(agg['by_level'], key=int):
        row = agg['by_level'][level]
        lines.append(f"L{level:<5} {row['runs']:<5} {row['rate'] * 100:>4.0f}%   {str(row['mean_cloud_tokens']):<11} {str(row['mean_seconds']):<7} {str(row['mean_calls']):<9} {row['mean_rounds']}")
    lines += ['', '任务              等级  结果（每次运行一个字符：✓通过 ✗未通过 ≠假通过 ■停止 ⏱超时 !出错 -跳过）']
    glyph = {PASS: '✓', FALSE_PASS: '≠', GATE_FAIL: '✗', STOPPED: '■', TIMEOUT: '⏱', ERROR: '!', SKIPPED: '-'}
    order = {}
    for record in result['records']:
        order.setdefault(record['task'], []).append(glyph.get(record['status'], '?'))
    for task, marks in order.items():
        level = next(r['level'] for r in result['records'] if r['task'] == task)
        lines.append(f"{task:<17} L{level:<4} {''.join(marks)}")
    mech = o['mechanisms']
    triggered = ' · '.join(f'{k}={v}' for k, v in mech.items() if v) or '（无）'
    lines += ['', '触发的 runtime 机制：' + triggered]
    return '\n'.join(lines)
