"""生成已知示例修复，不调用模型。 Generate the known demo repair without model inference."""

import argparse
import json
from pathlib import Path

from masa.domain.models import digest
from masa.infrastructure.workspaces import manifest


def build_patch(source: Path) -> dict:
    """为公开 Todo 缺陷生成确定性补丁。 Build a deterministic patch for the public Todo defect."""
    before = manifest(source)
    replacements = {
        'todo/item.go': [('package todo\n', 'package todo\n\nimport "strings"\n'),
                         ('return title', 'return strings.TrimSpace(title)')],
        'todo/service.go': [('if title == "" {', 'title = NormalizeTitle(title)\n\tif title == "" {')],
    }
    files = []
    for name, edits in replacements.items():
        content = (source / name).read_text(encoding='utf-8')
        for old, new in edits:
            if content.count(old) != 1:
                raise ValueError(f'fixture changed: expected one occurrence in {name}')
            content = content.replace(old, new)
        files.append({'path':name, 'before_sha256':before[name], 'content':content})
    return {'base_snapshot':digest(before), 'files':files}


def main():
    """输出修复提案供 runtime 校验，不修改源文件。 Emit a proposal for runtime validation without source edits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.cache/todo-patch.json'))
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / 'examples/go-todo'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_patch(source), ensure_ascii=False, indent=2), encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
