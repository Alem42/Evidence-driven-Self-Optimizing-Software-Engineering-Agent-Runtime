"""重新生成语言耦合基线。 Regenerate the language-coupling baseline.  用法 / usage:  python tests/golden/capture_coupling.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_language_coupling as t  # noqa: E402

data = t.scan()
t.BASELINE.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
print('files', len(data), 'hits', sum(sum(c.values()) for c in data.values()))
