"""Python 项目的静态检查（由运行器在隔离副本里执行）：逐个编译 .py 文件，语法错误按 `路径:行:列: 信息` 输出，退出码 1。
The Python static check (executed by the runner in the isolated copy): compile every .py file; a syntax error is printed as `path:line:col: message` and the exit code is 1.

只用标准库、不执行被检查的代码。 Standard library only; the checked code is never executed.
"""
import os
import sys


def main(root):
    failed = False
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__')
        for name in sorted(names):
            if not name.endswith('.py'):
                continue
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, root).replace(os.sep, '/')
            with open(path, 'rb') as handle:
                source = handle.read()
            try:
                compile(source, relative, 'exec', dont_inherit=True)
            except SyntaxError as exc:
                failed = True
                print(f'{relative}:{exc.lineno or 0}:{exc.offset or 0}: {type(exc).__name__}: {exc.msg}')
            except (ValueError, RecursionError) as exc:
                failed = True
                print(f'{relative}:0:0: {type(exc).__name__}: {exc}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else '.'))
