"""Python 的确定性检查（零 token）：语法、泄漏之外的就地检查、导入规范化、轮廓、导出声明。 Python's deterministic checks (zero tokens): syntax, in-stage checks beyond leaks, import normalisation, outline and export declarations.

全部用标准库 `ast` 在进程内完成，不需要任何外部工具链；这是 Python 包相对 Go 包的一个优势（Go 的语法检查要调用 gofmt）。
Everything runs in-process on the standard-library `ast`; no external toolchain is needed (the Go pack has to call gofmt for its parse check).
与 Go 包逐项对应：语法 ↔ gofmt -e；空转测试 ↔ testlint；入口规则 ↔ entrypoint；未知内部导入 ↔ imports；导入修复 ↔ goimports；轮廓 ↔ outline_go。
Item by item the counterparts of the Go pack: syntax <-> gofmt -e; vacuous tests <-> testlint; entry rule <-> entrypoint; unknown internal imports <-> imports; import fixing <-> goimports; outline <-> outline_go.
"""
import ast
import re
import sys

STDLIB = frozenset(sys.stdlib_module_names) | {'__future__'}


def is_test_path(path):
    """测试文件：tests/ 目录下，或文件名以 test_ 开头。 A test file: under tests/ or named test_*.py."""
    text = str(path).replace('\\', '/')
    return text.startswith('tests/') or '/tests/' in text or text.rsplit('/', 1)[-1].startswith('test_')


def parse(source):
    """ast.parse，失败返回 None。 ast.parse, or None on failure."""
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None


def parse_errors(files, paths):
    """{路径: 带行列号的语法错误}。 {path: syntax error with line:column}."""
    found = {}
    for path in paths:
        text = files.get(path)
        if not path.endswith('.py') or not isinstance(text, str):
            continue
        try:
            ast.parse(text)
        except SyntaxError as exc:
            found[path] = f'{path}:{exc.lineno or 0}:{exc.offset or 0}: {type(exc).__name__}: {exc.msg}'[:600]
        except (ValueError, RecursionError) as exc:
            found[path] = f'{path}:0:0: {type(exc).__name__}: {exc}'[:600]
    return found


# ───────────────────────── 导入 / imports ─────────────────────────
def imports_of(source):
    """[(模块全名, 层级, 导入的名字)]：`import a.b` -> ('a.b', 0, [])；`from .x import y` -> ('x', 1, ['y'])。 [(module, level, names)]."""
    tree = parse(source)
    out = []
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(alias.name, 0, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or '', node.level, [alias.name for alias in node.names]))
    return out


def planned_modules(planned_paths):
    """规格里的 .py 文件对应的模块点号路径集合：a/b.py -> a.b；a/b/__init__.py -> a.b（包）。 Dotted module paths of the planned .py files."""
    modules, packages = set(), set()
    for path in planned_paths:
        if not path.endswith('.py'):
            continue
        parts = path[:-3].split('/')
        if parts[-1] == '__init__':
            name = '.'.join(parts[:-1])
            if name:
                modules.add(name)
                packages.add(name)
        elif parts[-1] == '__main__':
            modules.add('.'.join(parts))
        else:
            modules.add('.'.join(parts))
    return modules, packages


def unknown_import_messages(files, paths, module, planned_paths, extra_allowed=()):
    """{路径: 说明}。导入了本项目里规格没有的模块（一定 ImportError），或导入了第三方包。 {path: explanation}: an import of a module of this project that the spec lacks (always an ImportError), or a third-party import."""
    modules, packages = planned_modules(planned_paths)
    importable = sorted(m for m in modules if m.startswith(module + '.') and not m.endswith('.__main__') and m != module)
    out = {}
    for path in paths:
        text = files.get(path)
        if not path.endswith('.py') or not isinstance(text, str):
            continue
        directory = path.rsplit('/', 1)[0].replace('/', '.') if '/' in path else ''
        bad, third = [], []
        for name, level, names in imports_of(text):
            if level:  # 相对导入：先还原成绝对路径（.=本包，..=上一层）/ relative import: make it absolute first (. = this package, .. = the parent)
                base = directory.split('.') if directory else []
                base = base[:max(len(base) - (level - 1), 0)]
                full = '.'.join(base + ([name] if name else []))
                if not full or not (full in modules or any(m.startswith(full + '.') for m in modules)):
                    bad.append('.' * level + name)
                continue
            top = name.split('.')[0]
            if top == module:
                if name not in modules and not any(m.startswith(name + '.') for m in modules):
                    bad.append(name)
            elif top not in STDLIB and top != 'tests' and top not in extra_allowed:
                third.append(name)
        problems = []
        if bad:
            problems.append('imports ' + ', '.join(sorted(set(bad))) + ', but the approved spec has no Python file for that module, so it can never import. '
                            'Import only these modules of this project: ' + (', '.join(importable) or '(none)')
                            + '. Do not invent a module: put the code in one of them or call the functions that already exist.')
        if third:
            problems.append('imports third-party package ' + ', '.join(sorted(set(third))) + '; only the Python standard library and this project are allowed')
        if problems:
            out[path] = ' '.join(problems)
    return out


# ───────────────────────── 入口规则 / entrypoint rule ─────────────────────────
TEMPLATE = ('from <module>.<package>.<file> import run (the full dotted path of the FILE that defines run); import sys; sys.exit(run(sys.argv[1:], sys.stdin, sys.stdout, sys.stderr)) '
            'and put everything else (reading stdin, parsing arguments, output formatting, error handling) in <module>.<app>.run, which the tests call with io.StringIO as stdin')


def _reads_input(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ('input', 'open'):
                found.add(func.id + '()')
            elif isinstance(func, ast.Attribute):
                owner = ast.unparse(func.value)
                if owner == 'sys.stdin':
                    found.add('sys.stdin.' + func.attr)
                elif owner == 'fileinput':
                    found.add('fileinput.' + func.attr)
        elif isinstance(node, ast.For) and ast.unparse(node.iter) == 'sys.stdin':
            found.add('for ... in sys.stdin')
    return sorted(found)


def entry_problem_messages(files, paths, entry):
    """入口文件必须是薄壳：不许自己读输入。 The entry file must be a thin wrapper: it may not read input itself."""
    out = {}
    for path in paths:
        text = files.get(path)
        if path != entry or not isinstance(text, str):
            continue
        tree = parse(text)
        found = _reads_input(tree) if tree is not None else []
        if found:
            out[path] = ('__main__.py reads or parses input itself (' + ', '.join(found[:4]) + '), so no test ever executes that code and wiring bugs pass the tests unnoticed. Write it as: ' + TEMPLATE)
    return out


# ───────────────────────── 空转测试 / vacuous tests ─────────────────────────
def test_problems(source):
    """一个测试文件的问题列表（空表示没问题）。 Problems of one test file (empty means fine)."""
    tree = parse(source)
    if tree is None:
        return []  # 语法错误由 parse_errors 报 / syntax errors are reported by parse_errors
    problems = []
    tests = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test')]
    if not tests:
        problems.append('the test file declares no test function or method (name starting with test), so nothing is tested')
        return problems
    asserting = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            test = node.test
            if not (isinstance(test, ast.Constant) and test.value):  # assert True 不算 / assert True does not count
                asserting = True
        elif isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            tail = name.rsplit('.', 1)[-1]
            if tail.startswith('assert') or tail in ('fail', 'assertRaises', 'assertRaisesRegex'):
                args = node.args
                if tail in ('assertTrue', 'assertIsNotNone') and args and isinstance(args[0], ast.Constant) and args[0].value:
                    continue
                asserting = True
            elif name in ('pytest.raises', 'pytest.fail', 'self.fail'):
                asserting = True
    if not asserting:
        problems.append('no test ever fails: add real assertions (self.assertEqual(actual, expected) or assert actual == expected) that fail when a result differs from the expectation')
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name.rsplit('.', 1)[-1] in ('skip', 'skipTest', 'skipIf', 'skipUnless', 'xfail') or name in ('unittest.skip', 'pytest.skip'):
                problems.append('tests must not be skipped (skip hides failures)')
                break
        elif isinstance(node, ast.Attribute) and node.attr in ('skip', 'skipIf', 'skipUnless', 'xfail') and ast.unparse(node.value) in ('unittest', 'pytest.mark'):
            problems.append('tests must not be skipped (skip hides failures)')
            break
    return problems


def test_problem_messages(files, paths):
    out = {}
    for path in paths:
        text = files.get(path)
        if path.endswith('.py') and is_test_path(path) and isinstance(text, str) and path.rsplit('/', 1)[-1] != '__init__.py':
            problems = test_problems(text)
            if problems:
                out[path] = '; '.join(problems)
    return out


# ───────────────────────── 导入规范化 / import normalisation ─────────────────────────
def _bound_names(tree):
    """文件里被绑定的顶层名字（赋值、def、class、导入、参数、推导变量…），用来判断“用到但没导入”。 Names bound anywhere in the file."""
    import builtins
    names = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if not isinstance(node, ast.ClassDef):
                a = node.args
                for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []):
                    names.add(arg.arg)
        elif isinstance(node, ast.Lambda):
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []):
                names.add(arg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split('.')[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
    return names


def fix_imports(source):
    """删掉未使用的导入名；`module.attr` 用到但没导入的标准库模块自动补一行 `import module`。返回 (新源码, [(动作, 名字)])。
    Remove unused imported names; add `import module` for a standard-library module used as `module.attr` but never imported. Returns (source, [(action, name)])."""
    tree = parse(source)
    if tree is None:
        return source, []
    lines = source.split('\n')
    changes = []
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    exported = set()
    for node in tree.body:  # __all__ 里的名字算使用 / names in __all__ count as used
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__all__' for t in node.targets):
            try:
                exported = set(ast.literal_eval(node.value))
            except (ValueError, SyntaxError):
                pass
    edits = []  # (起始行, 结束行, 新文本或 None) / (start, end, replacement lines or None)
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)) or node.end_lineno is None:
            continue
        if isinstance(node, ast.ImportFrom) and (node.module == '__future__' or any(a.name == '*' for a in node.names)):
            continue
        keep = []
        for alias in node.names:
            bound = (alias.asname or alias.name).split('.')[0]
            if bound in used or bound in exported:
                keep.append(alias)
            else:
                changes.append(('remove', alias.name))
        if len(keep) == len(node.names):
            continue
        if not keep:
            edits.append((node.lineno - 1, node.end_lineno, None))
        else:
            indent = lines[node.lineno - 1][:len(lines[node.lineno - 1]) - len(lines[node.lineno - 1].lstrip())]
            rendered = ', '.join(a.name + (f' as {a.asname}' if a.asname else '') for a in keep)
            text = f'{indent}import {rendered}' if isinstance(node, ast.Import) else f"{indent}from {'.' * node.level}{node.module or ''} import {rendered}"
            edits.append((node.lineno - 1, node.end_lineno, [text]))
    for start, end, new in sorted(edits, reverse=True):
        lines[start:end] = new if new is not None else []
    result = '\n'.join(lines)
    # 缺失的标准库导入 / missing standard-library imports
    tree2 = parse(result)
    if tree2 is not None:
        bound = _bound_names(tree2)
        wanted = sorted({n.value.id for n in ast.walk(tree2) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                         and isinstance(n.value.ctx, ast.Load) and n.value.id in STDLIB and n.value.id not in bound and not n.value.id.startswith('_')})
        if wanted:
            at = 0
            for node in tree2.body:  # 放在文档字符串与 __future__ 导入之后 / after the docstring and __future__ imports
                if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)) or \
                        (isinstance(node, ast.ImportFrom) and node.module == '__future__'):
                    at = node.end_lineno
                else:
                    break
            rows = result.split('\n')
            rows[at:at] = [f'import {name}' for name in wanted]
            result = '\n'.join(rows)
            changes += [('add', name) for name in wanted]
    return result, changes


# ───────────────────────── 轮廓与导出 / outline and exports ─────────────────────────
def outline(source):
    """确定性轮廓：模块文档首行、导入、顶层 def/class 签名（类含方法签名），去掉函数体。 A deterministic outline: signatures of top-level defs and classes (with method signatures), bodies removed."""
    tree = parse(source)
    if tree is None:
        return '\n'.join(l.rstrip() for l in source.splitlines() if re.match(r'(?:def|class|import|from|async def)\b', l))
    out = []

    def signature(node, indent):
        prefix = 'async def' if isinstance(node, ast.AsyncFunctionDef) else 'def'
        returns = f' -> {ast.unparse(node.returns)}' if node.returns else ''
        doc = ast.get_docstring(node)
        suffix = f'  # {doc.splitlines()[0][:80]}' if doc else ''
        return f'{indent}{prefix} {node.name}({ast.unparse(node.args)}){returns}: ...{suffix}'
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(ast.unparse(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(signature(node, ''))
        elif isinstance(node, ast.ClassDef):
            bases = f"({', '.join(ast.unparse(b) for b in node.bases)})" if node.bases else ''
            out.append(f'class {node.name}{bases}:')
            members = [signature(m, '    ') for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            out += members or ['    ...']
        elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            out.append(f"{', '.join(t.id for t in node.targets)} = ...")
    return '\n'.join(out)


def declared_names(source):
    """顶层声明的名字：def / class / 赋值 / 导入再导出。 Names declared at module level: defs, classes, assignments and re-exported imports."""
    tree = parse(source)
    names = set()
    if tree is None:
        return {m.group(1) for m in re.finditer(r'(?m)^(?:def|class)\s+(\w+)|^(\w+)\s*=', source) for m in [m] if m.group(1)}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split('.')[0] for a in node.names)
    return names


def declares(source, name):
    """契约检查：包的源码（可能是几个文件拼起来的）里是否声明了这个名字。 Contract check: does the package source (possibly several files joined) declare this name?"""
    if name in declared_names(source):
        return True
    # 几个文件拼接后可能不是合法模块（例如 __future__ 位置）：退回逐行匹配 / joined files may not parse as one module: fall back to a line match
    return bool(re.search(r'(?m)^(?:(?:async\s+)?def|class)\s+%s\b|^%s\s*[:=]' % (re.escape(name), re.escape(name)), source))


def test_function_source(files, path, line, limit=1500):
    """失败行所在的测试函数源码；找不到返回空串。 The source of the test function holding the failing line; empty when not found."""
    for name, text in files.items():
        if name == path or name.endswith('/' + str(path)):
            tree = parse(text)
            if tree is None or not 1 <= int(line or 0) <= len(text.splitlines()):
                return ''
            best = None
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= int(line) <= (node.end_lineno or node.lineno):
                    if best is None or node.lineno >= best.lineno:
                        best = node
            if best is None:
                return ''
            return '\n'.join(text.splitlines()[best.lineno - 1:best.end_lineno])[:limit]
    return ''


# ───────────────────────── 导入的名字是否存在 / do imported names exist ─────────────────────────
def _module_file(files, dotted):
    """点号模块名对应的文件（a.b -> a/b.py 或 a/b/__init__.py），没有返回 None。 The file a dotted module name resolves to, or None."""
    base = dotted.replace('.', '/')
    for candidate in (base + '.py', base + '/__init__.py'):
        if isinstance(files.get(candidate), str):
            return candidate
    return None


def _defining_files(files, name, top):
    return sorted(p for p, t in files.items() if p.endswith('.py') and p.startswith(top + '/') and isinstance(t, str) and not p.endswith('__init__.py')
                  and name in declared_names(t))


def name_import_problems(files, paths, module):
    """`from M import n`：M 是本项目已有的文件，却没有定义 n（也不是它的子模块）——一定 ImportError。生成一个文件时双向检查：它自己的导入，以及已有文件对它的导入。
    `from M import n` where M is an existing file of this project that does not define n (nor has a submodule n) always raises ImportError. Checked both ways when a file is written: its own imports, and the existing files' imports of it."""
    out = {}

    def add(path, message):
        out[path] = (out[path] + ' ' if path in out else '') + message

    def check(owner, text, rewrite_path):
        for name, level, names in imports_of(text):
            if level or not names or name.split('.')[0] != module:
                continue
            target = _module_file(files, name)
            if target is None or '*' in names:
                continue
            declared = declared_names(files[target])
            for item in names:
                if item in declared or _module_file(files, name + '.' + item) is not None:
                    continue
                hint = _defining_files(files, item, module)
                where = (' It is defined in ' + ', '.join(h[:-3].replace('/', '.') for h in hint[:3]) + ': import it from there.') if hint else ' Define it in that module or import something that exists.'
                add(rewrite_path, f"{owner} imports '{item}' from '{name}', but {target} does not define it.{where}")
    for path in paths:
        text = files.get(path)
        if not path.endswith('.py') or not isinstance(text, str) or path.endswith('__init__.py'):
            continue
        check(path, text, path)  # 它自己的导入 / its own imports
        dotted = path[:-3].replace('/', '.')
        for other, other_text in files.items():  # 别的文件对它的导入：要求它定义那些名字 / other files' imports of it: it must define those names
            if other != path and other.endswith('.py') and isinstance(other_text, str) and not other.endswith('__init__.py'):
                for name, level, names in imports_of(other_text):
                    if not level and name == dotted and '*' not in names:
                        declared = declared_names(text)
                        missing = [n for n in names if n not in declared and _module_file(files, name + '.' + n) is None]
                        if missing:
                            add(path, f"{other} imports {', '.join(repr(n) for n in missing)} from '{dotted}', so this file must define " + ('it' if len(missing) == 1 else 'them') + '.')
    return out
