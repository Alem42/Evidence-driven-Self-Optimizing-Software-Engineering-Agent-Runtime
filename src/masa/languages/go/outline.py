"""Go 源码的轮廓（签名摘要）。 A signature digest of Go source."""
import re

_DECLARATION = re.compile(r'^(package|import|func|type|const|var)\b')


def outline_go(source):
    """Go 源码的确定性轮廓：包名、import、顶层声明的**第一行**（函数签名、类型头），去掉函数体。 A deterministic outline of Go source: package, imports and the first line of each top-level declaration; bodies removed."""
    out, depth = [], 0
    for line in source.splitlines():
        if depth == 0 and _DECLARATION.match(line):
            out.append(line.rstrip().rstrip('{').rstrip())
        depth += line.count('{') - line.count('}')
        depth = max(depth, 0)
    return '\n'.join(out)
