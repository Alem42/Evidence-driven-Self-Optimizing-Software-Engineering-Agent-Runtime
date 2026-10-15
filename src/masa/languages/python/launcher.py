"""运行生成的项目：在当前目录里找到唯一带 __main__.py 的顶层包并以 `python -m 包名` 的方式运行，参数原样传入。
Run the generated project: find the single top-level package with a __main__.py in the current directory and run it as `python -m <package>`; arguments are passed through unchanged.

运行器用固定的启动器而不是 `-m 包名`，因为操作表是固定的，不能包含每个项目不同的包名。 The runner uses a fixed launcher instead of `-m <package>` because the operation table is fixed and cannot hold a per-project package name.
"""
import os
import runpy
import sys


def main():
    root = os.getcwd()
    packages = sorted(n for n in os.listdir(root) if os.path.isfile(os.path.join(root, n, '__main__.py')) and not n.startswith(('.', '_')) and n != 'tests')
    if len(packages) != 1:
        sys.stderr.write(f'expected exactly one top-level package with __main__.py, found {packages}\n')
        return 2
    sys.path.insert(0, root)
    sys.argv = [packages[0]] + sys.argv[1:]
    try:
        runpy.run_module(packages[0], run_name='__main__', alter_sys=True)
    except SystemExit as exc:
        code = exc.code
        if code is not None and not isinstance(code, int):
            sys.stderr.write(str(code) + chr(10))  # sys.exit("message") 的行为：消息写到 stderr，退出码 1 / sys.exit("message"): the message goes to stderr, status 1
        return code if isinstance(code, int) else (0 if code is None else 1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
