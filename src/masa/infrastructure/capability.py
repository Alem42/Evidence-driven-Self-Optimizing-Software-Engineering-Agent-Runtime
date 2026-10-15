"""启动时轻量检测本机能否跑本地模型（显卡显存 + 内存，Windows 与 Linux）。不够就禁用本地模型，避免在小型云服务器上崩溃。
Light start-up check: can this host run local models (GPU VRAM + RAM, Windows and Linux)? If not, local models are disabled so a small cloud server cannot be crashed.

规则刻意宽松——"理论上能部署"即可：NVIDIA 显存 >= 6 GB 且内存 >= 16 GB。云端小机器没有显卡，自然被挡住。
The rule is deliberately loose - "could run in theory": NVIDIA VRAM >= 6 GB and RAM >= 16 GB. Small cloud boxes have no GPU, so they are blocked.
环境变量 MASA_LOCAL_MODELS=0 强制禁用，=1 强制放行（检测不到但你知道能跑，例如 AMD/Apple），默认 auto。
Env MASA_LOCAL_MODELS: 0 forces off, 1 forces on (undetectable but you know it works, e.g. AMD/Apple), default auto.
"""
import os
import shutil
import subprocess

MIN_VRAM_GB = 6
MIN_RAM_GB = 16
GIB = 1 << 30


def ram_bytes():
    """物理内存总量；容器里取 cgroup 限制与物理内存的较小者。 Total RAM; in a container the smaller of the cgroup limit and physical RAM."""
    total = None
    try:
        if os.name == 'nt':
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong), ('avail', ctypes.c_ulonglong),
                            ('page_total', ctypes.c_ulonglong), ('page_avail', ctypes.c_ulonglong), ('virtual_total', ctypes.c_ulonglong),
                            ('virtual_avail', ctypes.c_ulonglong), ('extended', ctypes.c_ulonglong)]
            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total = int(status.total)
        else:
            with open('/proc/meminfo', encoding='ascii') as handle:
                for line in handle:
                    if line.startswith('MemTotal:'):
                        total = int(line.split()[1]) * 1024
                        break
            for path in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
                try:
                    with open(path, encoding='ascii') as handle:
                        text = handle.read().strip()
                    if text.isdigit() and total is not None:
                        total = min(total, int(text))
                except OSError:
                    pass
    except (OSError, ValueError, AttributeError):
        return None
    return total


def gpu_vram_bytes():
    """最大的一张 NVIDIA 显卡的显存；没有显卡或查询失败返回 None。 VRAM of the largest NVIDIA GPU, or None when absent or the query fails."""
    exe = shutil.which('nvidia-smi')
    if not exe:
        return None
    try:
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        out = subprocess.run([exe, '--query-gpu=memory.total', '--format=csv,noheader,nounits'], capture_output=True, text=True,
                             timeout=5, stdin=subprocess.DEVNULL, **options)
        sizes = [int(line.strip()) for line in out.stdout.splitlines() if line.strip().isdigit()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return max(sizes) * (1 << 20) if sizes else None


def detect(env=None, ram=ram_bytes, gpu=gpu_vram_bytes):
    """返回 {allowed, reason, vram_gb, ram_gb, mode}。 Returns {allowed, reason, vram_gb, ram_gb, mode}."""
    mode = (env if env is not None else os.environ).get('MASA_LOCAL_MODELS', 'auto').strip().lower()
    memory, vram = ram(), gpu()
    info = {'vram_gb': None if vram is None else round(vram / GIB, 1), 'ram_gb': None if memory is None else round(memory / GIB, 1), 'mode': mode}
    if mode in ('0', 'off', 'false', 'no'):
        return {**info, 'allowed': False, 'reason': 'disabled by MASA_LOCAL_MODELS=0'}
    if mode in ('1', 'on', 'true', 'yes'):
        return {**info, 'allowed': True, 'reason': 'forced on by MASA_LOCAL_MODELS=1'}
    if vram is None:
        return {**info, 'allowed': False, 'reason': 'no NVIDIA GPU detected (cloud/small hosts cannot run local models)'}
    if vram < MIN_VRAM_GB * GIB:
        return {**info, 'allowed': False, 'reason': f'GPU memory {info["vram_gb"]} GB < {MIN_VRAM_GB} GB'}
    if memory is None or memory < MIN_RAM_GB * GIB:
        return {**info, 'allowed': False, 'reason': f'RAM {info["ram_gb"]} GB < {MIN_RAM_GB} GB'}
    return {**info, 'allowed': True, 'reason': 'GPU and RAM are sufficient'}


# 进程启动时只检测一次；演示部署另外强制禁用。 Evaluated once per process; a demo deployment additionally forces it off.
STATE = {'allowed': True, 'reason': 'not evaluated'}


def configure(demo=False, **kwargs):
    STATE.clear()
    STATE.update(detect(**kwargs))
    if demo and STATE['allowed']:
        STATE.update(allowed=False, reason='read-only demo deployment')
    return STATE


def local_allowed():
    return bool(STATE['allowed'])
