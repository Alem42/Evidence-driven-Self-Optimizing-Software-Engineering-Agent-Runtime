"""生成演示用的状态目录：只复制评测结果（bench/results/*.json），并扫描密钥，发现任何密钥就中止。
Make a sanitized state directory for the demo: copies only the benchmark results and aborts if any API key string appears in them.

用法 / usage:  python scripts/ops/make_demo_state.py [--source .masa] [--out demo-state]
然后 / then:   MASA_STATE=./demo-state docker compose up -d      （目录需要对容器用户 uid 10001 可读写 / the directory must be readable and writable for uid 10001）
"""
import argparse
import json
import shutil
import sys
from pathlib import Path


def key_strings(source):
    """本地密钥文件里所有长度 >= 12 的字符串值。 Every string value of length >= 12 in the local key file."""
    path = source / 'provider-keys.local.json'
    found = set()
    if path.exists():
        def walk(value):
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, str) and len(value) >= 12:
                found.add(value)
        walk(json.loads(path.read_text(encoding='utf-8')))
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path('.masa'))
    parser.add_argument('--out', type=Path, default=Path('demo-state'))
    args = parser.parse_args()
    results = args.source / 'bench' / 'results'
    files = sorted(results.glob('*.json'))
    if not files:
        print(f'no benchmark results in {results}', file=sys.stderr)
        return 1
    secrets = key_strings(args.source)
    for file in files:
        text = file.read_text(encoding='utf-8', errors='replace')
        if any(secret in text for secret in secrets):
            print(f'ABORT: {file.name} contains a key string; nothing was written', file=sys.stderr)
            return 2
    target = args.out / 'bench' / 'results'
    target.mkdir(parents=True, exist_ok=True)
    for file in files:
        shutil.copy2(file, target / file.name)
    print(f'copied {len(files)} benchmark results to {target} (scanned against {len(secrets)} key strings; none found)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
