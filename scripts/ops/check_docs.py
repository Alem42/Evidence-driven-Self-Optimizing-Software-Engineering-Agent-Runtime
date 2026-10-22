"""文档里提到的代码路径必须存在：扫描 docs/ 下的 Markdown，凡是写成 `src/...`、`scripts/...`、`tests/...`、`frontend/...`、`runner/...` 的路径都要能在仓库里找到。
Every code path a document mentions must exist: scan the Markdown under docs/ and check each `src/...`, `scripts/...`, `tests/...`, `frontend/...`, `runner/...` path against the repository.

用法 / usage:  python scripts/ops/check_docs.py        （有缺失就返回非零 / exits non-zero when something is missing）
文档里有意提到“已删除”的路径时，用 ~~删除线~~ 或在同一行写“已删除 / removed / deleted”即可跳过。 A path that is deliberately mentioned as removed is skipped when the line says removed / deleted / 已删除 or uses ~~strikethrough~~.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = re.compile(r'`((?:src|scripts|tests|frontend|runner)/[A-Za-z0-9_./\-*]+?)(?::\d+)?`')
SKIP_WORDS = ('已删除', 'removed', 'deleted', '删除', '不再存在', '~~', '.env')  # .env 是用户自己建的可选文件 / .env is an optional file the user creates


def exists(path):
    if '*' in path:
        return any(ROOT.glob(path))
    return (ROOT / path.rstrip('/')).exists()


def main():
    missing = []
    for doc in sorted((ROOT / 'docs').rglob('*.md')):
        if 'personal' in doc.parts or 'evidence' in doc.parts:
            continue  # 个人材料；证据文档引用的是被评测的生成项目里的路径 / personal material; evidence documents quote paths inside the generated projects under test
        for number, line in enumerate(doc.read_text(encoding='utf-8').splitlines(), 1):
            if any(word in line for word in SKIP_WORDS):
                continue
            for match in PATH.finditer(line):
                if not exists(match.group(1)):
                    missing.append(f'{doc.relative_to(ROOT).as_posix()}:{number}: {match.group(1)}')
    for item in missing:
        print('MISSING', item)
    print(f'{len(missing)} missing path(s)')
    return 1 if missing else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
