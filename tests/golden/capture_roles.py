"""抓取黄金样本：把每个上下文的 instruction_for 与 response_schema 结果写入 roles_golden.json。
Capture the golden samples: write instruction_for and response_schema results for every context into roles_golden.json."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'src'))
sys.path.insert(0, str(HERE))

from masa.agents.protocol import instruction_for  # noqa: E402
from masa.agents.schemas import response_schema  # noqa: E402
from role_contexts import CONTEXTS  # noqa: E402


def render():
    return {name: {'instruction': instruction_for(context), 'schema': response_schema(context)} for name, context in CONTEXTS.items()}


if __name__ == '__main__':
    target = HERE / 'roles_golden.json'
    target.write_text(json.dumps(render(), ensure_ascii=False, indent=1), encoding='utf-8')
    print('wrote', target, len(CONTEXTS), 'contexts')
