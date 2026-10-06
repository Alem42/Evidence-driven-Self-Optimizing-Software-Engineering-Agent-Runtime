"""打印某任务版本链上每次验证的关键失败输出。 Print the key failure output of every verification in a task lineage."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.infrastructure.store import Store  # noqa: E402


def main(state, rid, limit=14):
    s = Store(Path(state))
    runs = {r['id']: r for r in s.all_runs()}
    chain = []
    while rid in runs:
        chain.append(runs[rid])
        rid = runs[rid]['data'].get('parent_run_id')
    for r in reversed(chain):
        d = r['data']
        plan = d.get('project_plan') or {}
        if not d.get('project_bundle'):
            print('--', r['id'][:8], plan.get('kind') or 'plan', plan.get('status'), plan.get('error') or '', 'changed=', plan.get('changed_files'))
            continue
        print('== VERIFY', r['id'][:8], r['status'])
        for t in s.tools(r['id']):
            if not t['result_ref']:
                continue
            res = s.read(t['result_ref'])
            if res.get('exit_code') == 0:
                print('  ', t['step_id'], 'ok')
                continue
            out = []
            for line in (res.get('stdout', '') + '\n' + res.get('stderr', '')).split('\n'):
                try:
                    j = json.loads(line)
                    line = j.get('Output', '') if isinstance(j, dict) else line
                except ValueError:
                    pass
                if line.strip() and not line.startswith(('=== ', '--- PASS', 'PASS')):
                    out.append(line.rstrip().replace(str(ROOT), '.'))
            print('  ', t['step_id'], 'FAIL'); print('\n'.join('      ' + x for x in out[:limit]))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 14)
