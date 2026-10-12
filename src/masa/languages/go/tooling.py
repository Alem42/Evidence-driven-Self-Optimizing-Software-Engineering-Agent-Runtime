"""Go 的确定性工具：gofmt 规范化、gofmt -e 语法检查、go test 输出解析。 Go's deterministic tools: gofmt normalisation, the gofmt -e parse check and go test output parsing.

这些原来写在 application/generation.py 里；搬到这里之后，核心不再知道 gofmt 的存在，只通过语言包询问。
These used to live in application/generation.py; now the core does not know gofmt exists and only asks the language pack.
行为逐字节保持不变（见 tests/test_local_generation.py、tests/test_project_generation.py）。
Behaviour is byte-for-byte unchanged (see tests/test_local_generation.py and tests/test_project_generation.py).
"""
import json
import os
import re
import subprocess
from pathlib import Path

from masa.infrastructure.proc import NO_WINDOW
from masa.languages.go.entrypoint import entry_problem_messages
from masa.languages.go.goimports import fix_imports, fix_module_imports
from masa.languages.go.imports import unknown_import_messages
from masa.languages.go.testlint import test_problem_messages

# 失败证据里值得保留的诊断行。 Diagnostic lines worth keeping in failure evidence.
FAILURE_MARKERS = r'(?i)(?:\.go:\d+|no Go files in |import cycle|FAIL|panic:|undefined:|imported and not used|expected|want |exit code|build failed)'
_LOCATION = r'([\w./\\-]+\.go):(\d+)(?::\d+)?'
_DECLARATION = r'(?m)^(?:func(?:\s*\([^)]*\))?\s+%s\b|type\s+%s\b|var\s+%s\b|const\s+%s\b|\t%s\b)'


def gofmt_path(executor):
    """工具链里的 gofmt；没有 go 就返回 None。 gofmt next to the configured go, or None."""
    go = getattr(executor, 'go_executable', None)
    if not go:
        return None
    return Path(go).parent / ('gofmt.exe' if os.name == 'nt' else 'gofmt')


def normalize(executor, files, paths, imports=True):
    """用工具链 gofmt 规范指定的 Go 文件；失败（如语法错误）则保持原样，让真实编译检查暴露问题。返回 (新文件, {路径: 导入改动})。
    Normalize the given Go files with the toolchain gofmt; on failure keep them as-is. Returns (files, {path: import changes}).
    imports=True 时先做确定性的 import 修复（补缺失/删多余，见 goimports），不调用模型。 With imports=True, deterministic import fixing runs first, with no model call."""
    formatter = gofmt_path(executor)
    if formatter is None or not formatter.is_file():
        return files, {}
    out = dict(files)
    fixed_imports = {}
    # 批准的 module 路径与本项目的包目录：用来修“module 前缀写错”的内部 import。 The approved module and this project's package directories.
    module_line = next((line.split(None, 1)[1].strip() for line in str(files.get('go.mod', '')).splitlines() if line.startswith('module ')), None)
    package_dirs = sorted({k.rsplit('/', 1)[0] for k in files if k.endswith('.go') and '/' in k and k.rsplit('/', 1)[0] != 'cmd/app'})
    for path in paths:
        if not path.endswith('.go') or not isinstance(files.get(path), str):
            continue
        if imports:
            source, changes = fix_imports(files[path])
            source, module_changes = fix_module_imports(source, module_line, package_dirs)
            changes = changes + module_changes
        else:
            source, changes = files[path], []
        try:
            process = subprocess.run([str(formatter)], input=source.encode('utf-8'),
                                     capture_output=True, timeout=10, check=False, **NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if process.returncode == 0 and 0 < len(process.stdout) <= 60000:
            out[path] = process.stdout.decode('utf-8')
            if changes:
                fixed_imports[path] = [f'{kind} {name}' for kind, name in changes]
    return out, fixed_imports


def parse_errors(executor, files, paths):
    """用 gofmt -e 做确定性语法检查（不需要编译整个项目），返回 {路径: 带行列号的错误}。工具链缺失时视为无错误。
    Deterministic parse check with `gofmt -e`: {path: error text with line:column}. Missing toolchain = no errors."""
    found = {}
    formatter = gofmt_path(executor)
    if formatter is None or not formatter.is_file():
        return found
    for path in paths:
        if not path.endswith('.go') or not isinstance(files.get(path), str):
            continue
        try:
            process = subprocess.run([str(formatter), '-e'], input=files[path].encode('utf-8'),
                                     capture_output=True, timeout=10, check=False, **NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if process.returncode != 0:
            text = process.stderr.decode('utf-8', 'replace').replace('<standard input>', path)
            found[path] = (found[path] + chr(10) if path in found else '') + chr(10).join(text.strip().splitlines()[:5])[:600]
    return found


def static_messages(files, paths, spec=None):
    """不需要工具链的就地检查：空转测试、读输入的 main.go、导入了规格里没有的内部包。 Toolchain-free in-stage checks: vacuous tests, a main.go that reads input, imports of packages the spec lacks."""
    unknown = unknown_import_messages(files, paths, spec['module'], [i['path'] for i in spec['files']]) if spec else {}
    return {**test_problem_messages(files, paths), **entry_problem_messages(files, paths), **unknown}


def declares(source, name):
    """包的源码里是否声明了这个导出名（契约检查）。 Does the package source declare this exported name (contract check)?"""
    return bool(re.search(_DECLARATION % ((re.escape(name),) * 5), source))


def format_text(executor, text):
    """用 gofmt 格式化一段源码；失败返回 None。 Format one source text with gofmt, or None on failure."""
    formatter = gofmt_path(executor)
    if formatter is None or not formatter.is_file():
        return None
    try:
        process = subprocess.run([str(formatter)], input=text.encode('utf-8'), capture_output=True, timeout=10, check=False, **NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0 or len(process.stdout) > 60000:
        return None
    return process.stdout.decode('utf-8')


def output_lines(text):
    """go test -json 的每一帧取 Output 字段，其余原样。 Take the Output field of each `go test -json` frame, other lines as they are."""
    for line in str(text or '').splitlines():
        try:
            frame = json.loads(line)
            value = frame.get('Output', '') if isinstance(frame, dict) else ''
        except (ValueError, TypeError):
            value = line
        yield from str(value).splitlines()
