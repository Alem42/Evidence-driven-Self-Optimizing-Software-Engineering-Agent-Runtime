"""内部包导入检查（零 token、确定性）：文件导入了项目内部的包，但规格里根本没有那个目录的 Go 文件——这种导入一定编译失败。
Internal import check (zero tokens, deterministic): a file imports a package of the project itself, but the spec has no Go file in that directory; such an import can never compile.

起因（第一次 10 文件项目的真实运行，bank）：第 1 轮 `main.go` 导入了 `example.com/bank/internal/app`，规格里只有 parser / store / rules / report 四个包，整轮验证被浪费，
并且错误被归到 `module_path`，要靠修复调用才能纠正。现在在文件刚生成、模型上下文还在的时候就带着“只能导入这些包”重写，和已有的语法就地重写是同一机制。
Origin (the first 10-file real run, bank): round 1's main.go imported example.com/bank/internal/app although the spec only has the parser / store / rules / report packages; a whole verification round was wasted and the
error needed a repair call. The file is now rewritten right after it is written, with the list of importable packages, through the same mechanism as the in-place syntax rewrite.
"""
import re

_BLOCK = re.compile(r'(?m)^\s*import\s*\(([^)]*)\)')
_SINGLE = re.compile(r'(?m)^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"')
_QUOTED = re.compile(r'"([^"]+)"')


def imports_of(source):
    """文件导入的全部路径。 Every import path of a file."""
    found = [m.group(1) for m in _SINGLE.finditer(source)]
    for block in _BLOCK.finditer(source):
        found += [m.group(1) for m in _QUOTED.finditer(re.sub(r'//[^\n]*', '', block.group(1)))]
    return found


def package_dirs(planned_paths):
    """规格里有 Go 文件的目录（根目录用空串）。 The directories that hold a Go file in the spec (the root is the empty string)."""
    return {p.rsplit('/', 1)[0] if '/' in p else '' for p in planned_paths if p.endswith('.go')}


def unknown_import_messages(files, paths, module, planned_paths):
    """{路径: 说明}。只检查导入了本项目模块路径下、但规格里没有对应目录的情况；标准库与第三方不管。
    {path: explanation}; only imports under this project's module whose directory the spec does not contain are reported; the standard library and third-party paths are not judged."""
    dirs = package_dirs(planned_paths)
    importable = sorted(d for d in dirs if d and not d.startswith('cmd/'))
    out = {}
    for path in paths:
        text = files.get(path)
        if not path.endswith('.go') or not isinstance(text, str):
            continue
        bad = []
        for imported in imports_of(text):
            if imported == module or imported.startswith(module + '/'):
                directory = imported[len(module) + 1:] if imported != module else ''
                if directory not in dirs:
                    bad.append(imported)
        if bad:
            out[path] = ('imports ' + ', '.join(sorted(set(bad))) + ', but the approved spec has no Go file in that directory, so it can never compile. '
                         'Import only these packages of this project: ' + (', '.join(f'{module}/{d}' for d in importable) or '(none)')
                         + '. Do not invent a package: put the code in one of them or call the functions that already exist.')
    return out
