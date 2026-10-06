"""入口检查：CLI 的 main.go 必须是薄薄的一层，读取输入和解析参数的逻辑要放进内部包。
Entrypoint check: the CLI main.go must be a thin wrapper; reading input and parsing arguments belongs in an internal package.

起因（评测里 4 个“假通过”全是这一类）：系统的测试只覆盖内部包，`main.go` 从来没被执行过，所以 main 里的接线错误能一路通过 go test / go vet：
`wc` 的 main 用 `io.ReadFull(os.Stdin, input)` 读进一个 nil 切片（永远读到 0 字节，输出 0 0 0）；`lru` 的 main 只读了 stdin 的第一行，容量还写死成 1。
Origin (all four "false passes" in the first hybrid benchmark): tests only cover internal packages and main.go is never executed, so wiring bugs in main pass go test and
go vet. wc's main did `io.ReadFull(os.Stdin, input)` into a nil slice (always 0 bytes, output "0 0 0"); lru's main read only the first line of stdin with a hard-coded capacity.

规则是确定性的：main.go 里不允许出现读 stdin / 解析输入的调用。把这些放进内部包函数（测试用 strings.NewReader 就能驱动），main 只负责传入 os.Stdin 并 os.Exit。
The rule is deterministic: main.go may not read stdin or parse input; that lives in an internal package function (tests drive it with strings.NewReader) and main only passes os.Stdin through and exits.
"""
import re

from masa.application.checks.goimports import _mask

# 在 main.go 里读输入/解析输入的调用。 Calls that read or parse input inside main.go.
_READING = re.compile(r'\b(?:bufio\.New(?:Scanner|Reader)|io\.(?:ReadAll|ReadFull|Copy)|ioutil\.ReadAll|os\.ReadFile|fmt\.(?:Fscan\w*|Scan\w*)|strings\.NewReader|csv\.NewReader|json\.NewDecoder)\b'
                      r'|\b\w+\.(?:Scan|ReadString|ReadLine|ReadRune|ReadByte)\s*\(')

TEMPLATE = ('func main() { os.Exit(<package>.Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) } '
            'and put everything else (reading stdin, parsing arguments, output formatting, error handling) in <package>.Run, which the tests call with a strings.NewReader as stdin')


def entry_problem_messages(files, paths=None):
    """{路径: 说明}。只检查 cmd/*/main.go。 {path: explanation}; only cmd/*/main.go is checked."""
    out = {}
    for path in (paths if paths is not None else files):
        if not (path.startswith('cmd/') and path.endswith('/main.go')) or not isinstance(files.get(path), str):
            continue
        found = sorted({m.group(0) for m in _READING.finditer(_mask(files[path]))})
        if found:
            out[path] = ('main.go reads or parses input itself (' + ', '.join(found[:4]) + '), so no test ever executes that code and wiring bugs pass go test unnoticed. Write it as: '
                         + TEMPLATE)
    return out
