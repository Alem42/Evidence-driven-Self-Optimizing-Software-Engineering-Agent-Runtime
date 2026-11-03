"""列出一个状态目录里所有失败的模型调用：角色、模型、错误原文、输出开头。用来回答“这个模型为什么接不住这个角色”。
List every failed model call in a state directory: role, model, the error text and the start of the output. It answers "why can this model not play this role".

用法 / usage:  python scripts/eval/call_errors.py <state dir> [--chars 600]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.infrastructure.store import Store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('state', type=Path)
    ap.add_argument('--chars', type=int, default=600)
    ap.add_argument('--all', action='store_true', help='也列出成功的调用 / list successful calls too')
    args = ap.parse_args()
    store = Store(args.state)
    try:
        for run in store.all_runs():
            for event in store.events(run['id']):
                payload = event['payload'] if isinstance(event.get('payload'), dict) else {}
                if event['type'] not in ('agent_call_failed', 'agent_call_completed', 'role_call_failed', 'model_call_failed'):
                    continue
                if event['type'].endswith('completed') and not args.all:
                    continue
                text = ''
                ref = payload.get('response_ref') or payload.get('raw_ref')
                if ref:
                    try:
                        raw = store.read(ref)
                        text = raw if isinstance(raw, str) else str(raw)
                    except Exception as exc:  # noqa: BLE001
                        text = f'<unreadable: {exc}>'
                print(f"{run['id'][:6]} {event['type']} {payload.get('purpose') or payload.get('role')} {payload.get('model')} :: {str(payload.get('error') or payload.get('reason') or '')[:args.chars]}")
                if text:
                    print('   output: ' + text[:args.chars].replace('\n', ' | '))
    finally:
        store.close()


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
