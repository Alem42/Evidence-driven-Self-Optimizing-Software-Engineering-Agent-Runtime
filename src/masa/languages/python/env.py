"""项目内的 Python 环境：像 Go 工具链放在 `.tools/go` 一样，生成项目用的 Python 放在 `.tools/pyenv`（一个虚拟环境）。
The project's own Python environment: like the Go toolchain in `.tools/go`, the Python that generated projects run on lives in `.tools/pyenv` (a virtual environment).

为什么单独一套（而不是用运行 masa 的那个 Python）/ Why a separate one (not the Python that runs masa):
- 生成的项目装的包只会进这个环境，不会弄脏 masa 自己的环境，更不会碰系统环境；删掉目录就回到干净状态。
  Packages a generated project needs go into this environment only: masa's own environment and the system's are never touched, and deleting the directory resets everything.
- 解释器与包是可复现的一部分（指纹里有解释器哈希），评测和重放要有一个固定的环境。
  The interpreter and packages are part of reproducibility (the fingerprint holds the interpreter hash); evaluations and replays need a fixed environment.

安装包的规则 / Rules for installing packages:
- 只允许 PyPI 上的“名字（可带 ==版本）”，不接受 URL、本地路径、命令行选项、extras：requirement 字符串一律先过正则。 Only plain PyPI names (optionally ==version); URLs, paths, options and extras are rejected before anything runs.
- 只装预编译的 wheel（`--only-binary=:all:`）：源码包的安装会执行包作者的 setup.py，这是任意代码执行。 Wheels only (`--only-binary=:all:`): installing an sdist runs the author's setup.py, which is arbitrary code execution.
- 环境大小有上限：装完超过上限就把这次新装的包卸掉并报告。 The environment has a size cap: when an install goes over it, the newly installed packages are removed again and the failure is reported.
- 策略（询问 / 完全访问 / 禁止）由调用方（console）判断，这里只做“能不能装、装多大”。 The policy (ask / full access / deny) is decided by the caller (the console); this module only does the "can it be installed, how big is it".
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from masa.domain.models import MasaError

NO_WINDOW = {'creationflags': 0x08000000} if sys.platform == 'win32' else {}
DEFAULT_MAX_MB = 500
INSTALL_TIMEOUT = 600
# 一个 requirement：包名 + 可选的 ==精确版本。没有 URL、路径、选项、extras、环境标记。 A requirement: a name plus an optional ==exact version; no URL, path, option, extra or marker.
REQUIREMENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,60}(==[A-Za-z0-9][A-Za-z0-9.!+_-]{0,30})?$')
# 这些是环境自带的基础设施，不算“项目依赖”，也不让生成的代码去导入。 Infrastructure shipped with every environment: not a project dependency and never offered to generated code.
INFRASTRUCTURE = frozenset({'pip', 'setuptools', 'pkg_resources', '_distutils_hack', 'wheel', 'distutils-precedence'})


def tools_dir():
    return Path(os.environ.get('MASA_TOOLS_DIR') or Path(__file__).resolve().parents[4] / '.tools')


def env_dir():
    return tools_dir() / 'pyenv'


def env_python():
    directory = env_dir()
    return directory / 'Scripts' / 'python.exe' if sys.platform == 'win32' else directory / 'bin' / 'python'


def exists():
    return env_python().is_file()


def size_mb(path=None):
    """目录占用的 MB（不跟随符号链接）。 Disk usage of a directory in MB (symlinks are not followed)."""
    total = 0
    for root, _dirs, files in os.walk(path or env_dir()):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return round(total / 1_000_000, 1)


def _clean_env():
    """子进程的环境变量：去掉会把 masa 自己的环境泄漏进项目环境的那几个（PYTHONPATH 会让 pip 把 masa 自己也列成“已安装的包”）。 The child's environment without the variables that would leak masa's own environment into the project's (PYTHONPATH made pip list masa itself as an installed package)."""
    return {k: v for k, v in os.environ.items() if k.upper() not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTHONSTARTUP', 'PIP_REQUIRE_VIRTUALENV')}


def _run(args, timeout):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, stdin=subprocess.DEVNULL, env=_clean_env(), **NO_WINDOW)


def clean_requirements(items):
    """校验并去重；任何一项不合规就整个拒绝（不静默丢掉）。 Validate and de-duplicate; one bad item rejects the whole list (nothing is dropped silently)."""
    if not isinstance(items, (list, tuple)) or not items or len(items) > 20:
        raise MasaError('packages must be a list of 1 to 20 names')
    out = []
    for item in items:
        text = str(item).strip()
        if not REQUIREMENT.match(text):
            raise MasaError(f'not an allowed package name: {text[:60]!r} (only a PyPI name, optionally ==version)')
        if text.lower() not in (o.lower() for o in out):
            out.append(text)
    return out


def create(base=None):
    """创建环境（已存在就什么也不做）。base = 用哪个 Python 当底座，默认是运行 masa 的这个。 Create the environment (a no-op when it exists); base is the Python it is built from."""
    if exists():
        return status()
    env_dir().parent.mkdir(parents=True, exist_ok=True)
    done = _run([base or sys.executable, '-m', 'venv', env_dir()], 300)
    if done.returncode != 0 or not exists():
        shutil.rmtree(env_dir(), ignore_errors=True)
        raise MasaError('could not create the project Python environment: ' + (done.stderr or done.stdout)[-300:].strip())
    return status()


def reset(base=None):
    """删掉重建：回到只有 pip 的干净状态。 Delete and recreate: back to a clean state with only pip."""
    shutil.rmtree(env_dir(), ignore_errors=True)
    _TOP.clear()
    return create(base)


def packages():
    """[{name, version}]，不含基础设施。 [{name, version}] without the infrastructure."""
    if not exists():
        return []
    done = _run([env_python(), '-m', 'pip', 'list', '--format=json', '--disable-pip-version-check'], 120)
    if done.returncode != 0:
        return []
    try:
        listed = json.loads(done.stdout)
    except ValueError:
        return []
    return sorted(({'name': p['name'], 'version': p['version']} for p in listed if str(p.get('name', '')).lower() not in INFRASTRUCTURE), key=lambda p: p['name'].lower())


_TOP = {}


def top_levels():
    """已安装的包能 import 的顶层名字（例如 scikit-learn → sklearn）；静态检查靠它放行。按环境目录的修改时间缓存。 The top-level import names of the installed packages (scikit-learn -> sklearn); the static check lets them through. Cached by the environment's modification time."""
    if not exists():
        return frozenset()
    stamp = max((p.stat().st_mtime_ns for p in env_dir().rglob('site-packages') if p.is_dir()), default=0)
    key = (str(env_python()), stamp)
    if key not in _TOP:
        code = 'import importlib.metadata as m, json; print(json.dumps(sorted(m.packages_distributions())))'
        done = _run([env_python(), '-c', code], 60)
        try:
            names = json.loads(done.stdout) if done.returncode == 0 else []
        except ValueError:
            names = []
        _TOP.clear()
        _TOP[key] = frozenset(n for n in names if n not in INFRASTRUCTURE and not n.startswith('_'))
    return _TOP[key]


def status(max_mb=DEFAULT_MAX_MB):
    if not exists():
        return {'exists': False, 'path': str(env_dir()), 'python': str(env_python()), 'size_mb': 0, 'max_mb': max_mb, 'over_limit': False, 'packages': [], 'version': ''}
    try:
        version = _run([env_python(), '-c', 'import sys; print(sys.version.split()[0])'], 30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        version = ''
    used = size_mb()
    return {'exists': True, 'path': str(env_dir()), 'python': str(env_python()), 'version': version, 'size_mb': used, 'max_mb': max_mb, 'over_limit': used > max_mb, 'packages': packages()}


def install(items, max_mb=DEFAULT_MAX_MB, run=_run):
    """装进项目环境。返回 {installed: [...], size_mb}；超过大小上限就卸掉这次新装的并抛错。run 可以注入（测试不联网）。
    Install into the project environment. Returns {installed, size_mb}; over the size cap the newly installed packages are removed and an error is raised. `run` can be injected (tests never touch the network)."""
    wanted = clean_requirements(items)
    if not exists():
        create()
    before = {p['name'].lower(): p['version'] for p in packages()}
    done = run([env_python(), '-m', 'pip', 'install', '--no-input', '--disable-pip-version-check', '--only-binary=:all:', *wanted], INSTALL_TIMEOUT)
    _TOP.clear()
    if done.returncode != 0:
        raise MasaError('pip install failed: ' + (done.stderr or done.stdout)[-400:].strip())
    after = packages()
    added = [p for p in after if p['name'].lower() not in before or before[p['name'].lower()] != p['version']]
    used = size_mb()
    if used > max_mb:
        fresh = [p['name'] for p in added if p['name'].lower() not in before]
        if fresh:
            run([env_python(), '-m', 'pip', 'uninstall', '-y', '--disable-pip-version-check', *fresh], 300)
        _TOP.clear()
        raise MasaError(f'the environment would be {used} MB, over the {max_mb} MB cap; the new packages were removed again (raise the cap in Settings -> Languages -> Python if you want them)')
    return {'installed': [f"{p['name']}=={p['version']}" for p in added], 'size_mb': used}


def discover_interpreters():
    """本机上能找到的其它 Python（py 启动器、PATH），给设置页选择用；只读，不运行它们。 Other Pythons found on this machine (the py launcher, PATH) for the Settings page to choose from; read-only, none of them is run."""
    found = []
    if sys.platform == 'win32':
        try:
            done = _run(['py', '-0p'], 15)
            for line in done.stdout.splitlines():
                match = re.search(r'-V:(?P<tag>\S+)\s+\*?\s*(?P<path>[A-Za-z]:\\.+python\.exe)\s*$', line.strip()) or re.search(r'^-?(?P<tag>\S*?)\s+(?P<path>[A-Za-z]:\\.+python\.exe)\s*$', line.strip())
                if match:
                    found.append({'path': match.group('path'), 'label': 'Python ' + match.group('tag').lstrip('-V:')})
        except (OSError, subprocess.TimeoutExpired):
            pass
    for name in ('python', 'python3'):
        path = shutil.which(name)
        if path and 'WindowsApps' not in path and not any(os.path.normcase(path) == os.path.normcase(f['path']) for f in found):  # WindowsApps 里的是商店的占位程序，不是真的 Python / the WindowsApps one is the Store's placeholder, not a real Python
            found.append({'path': path, 'label': f'PATH 上的 {name}'})
    own = str(Path(sys.executable))
    if not any(os.path.normcase(own) == os.path.normcase(f['path']) for f in found):
        found.append({'path': own, 'label': '运行 masa 的 Python（不建议用于生成项目）'})
    seen, out = set(), []
    for item in found:
        key = os.path.normcase(item['path'])
        if key not in seen and os.path.isfile(item['path']):
            seen.add(key)
            out.append(item)
    return out[:10]


def pick_file(title='选择 Python 解释器', patterns=(('Python', 'python*.exe python3'), ('所有文件', '*'))):
    """在**运行 masa 的这台电脑**上弹出系统的“打开文件”对话框，返回所选路径（取消返回空串）。对话框放在子进程里：tkinter 必须在主线程，而服务器线程不是。
    Opens the system "open file" dialog on the machine that runs masa and returns the chosen path (empty when cancelled). It runs in a child process because tkinter needs the main thread."""
    code = ('import json, sys, tkinter\nfrom tkinter import filedialog\nroot = tkinter.Tk()\nroot.withdraw()\nroot.attributes("-topmost", True)\n'
            'path = filedialog.askopenfilename(title=sys.argv[1], filetypes=json.loads(sys.argv[2]))\nprint(path)\n')
    try:
        done = _run([sys.executable, '-c', code, title, json.dumps([list(p) for p in patterns])], 300)
    except (OSError, subprocess.TimeoutExpired):
        raise MasaError('the file dialog could not be opened on this machine') from None
    if done.returncode != 0:
        raise MasaError('the file dialog is not available on this machine (tkinter missing or no display)')
    return done.stdout.strip()
