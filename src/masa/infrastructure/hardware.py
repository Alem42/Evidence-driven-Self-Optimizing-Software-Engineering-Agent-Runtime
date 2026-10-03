"""只读宿主机硬件状态；不会发送给模型。 Read-only host metrics, never model context."""

import copy
import csv
from datetime import datetime, timezone
import io
import json
import os
import shutil
import subprocess
import threading
import time


_OUTPUT_LIMIT = 32_768
_GPU_FIELDS = 'index,name,memory.total,memory.used,memory.free,utilization.gpu'
_MEMORY_QUERY = (
    "$ErrorActionPreference='Stop';"
    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
    "$computer=Get-CimInstance Win32_ComputerSystem;"
    "$modules=@(Get-CimInstance Win32_PhysicalMemory | "
    "Select-Object Capacity,Speed,ConfiguredClockSpeed);"
    "@{total_bytes=$computer.TotalPhysicalMemory;modules=$modules} | "
    "ConvertTo-Json -Depth 4 -Compress"
)


class _ProbeError(Exception):
    """只传递固定诊断码，不暴露命令原始错误。 Carry safe diagnostic codes only."""


def _capture(command, timeout):
    """限时且限输出运行固定查询，不启动 shell。 Run a bounded fixed query without a shell."""
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, shell=False, **options) as process:
            expired = threading.Event()

            def stop():
                # 超时中止只读查询；不请求管理员权限，也不重试。
                # Stop the read-only query on timeout, without elevation or retries.
                expired.set()
                try:
                    process.kill()
                except OSError:
                    pass

            timer = threading.Timer(timeout, stop)
            timer.daemon = True
            timer.start()
            try:
                output = process.stdout.read(_OUTPUT_LIMIT + 1)
                if len(output) > _OUTPUT_LIMIT:
                    process.kill()
                    raise _ProbeError('output_limit')
                process.wait(timeout=1)
                if expired.is_set():
                    raise _ProbeError('timeout')
                if process.returncode != 0:
                    raise _ProbeError('query_failed')
                return output.decode('utf-8-sig', errors='strict')
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=1)
    except _ProbeError:
        raise
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise _ProbeError('query_failed') from None


def _number(value):
    """缺失或不合法数字显示未知，绝不填造零。 Represent unavailable numbers as unknown, not zero."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = int(str(value).strip())
    except (ValueError, TypeError):
        return None
    return result if result >= 0 else None


def _gpu_result(raw):
    """解析固定 GPU CSV 字段，显存单位明确为 MiB。 Parse the fixed GPU CSV with explicit MiB units."""
    devices = []
    for fields in csv.reader(io.StringIO(raw)):
        if not fields or not any(field.strip() for field in fields):
            continue
        if len(fields) != 6 or len(devices) >= 32:
            raise _ProbeError('invalid_response')
        index, name, total, used, free, utilization = fields
        name = name.strip()
        if not name or len(name) > 200 or _number(index) is None:
            raise _ProbeError('invalid_response')
        percent = _number(utilization)
        devices.append({
            'index': _number(index), 'name': name,
            'total_mib': _number(total), 'used_mib': _number(used), 'free_mib': _number(free),
            'utilization_percent': percent if percent is not None and percent <= 100 else None,
        })
    return {'available': bool(devices), 'devices': devices,
            'warning': None if devices else 'no_devices'}


def _memory_result(raw):
    """内存总量与条目分开；频率沿用 CIM 固件报告值。 Keep host totals separate from firmware module data."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise _ProbeError('invalid_response') from None
    if not isinstance(data, dict) or not isinstance(data.get('modules'), list):
        raise _ProbeError('invalid_response')
    total = _number(data.get('total_bytes')) or None
    modules = []
    if len(data['modules']) > 128:
        raise _ProbeError('invalid_response')
    for item in data['modules']:
        if not isinstance(item, dict):
            raise _ProbeError('invalid_response')
        modules.append({'capacity_bytes': _number(item.get('Capacity')) or None,
                        'speed_mhz': _number(item.get('Speed')) or None,
                        'configured_speed_mhz': _number(item.get('ConfiguredClockSpeed')) or None})
    return {'available': total is not None or bool(modules), 'total_bytes': total,
            'modules': modules, 'warning': None if total is not None else 'partial_data'}


class HardwareMonitor:
    def __init__(self, cache_seconds=10, timeout_seconds=4):
        """短期缓存避免轮询反复启动进程。 Cache short-lived metrics to avoid repeated polling processes."""
        self.cache_seconds = cache_seconds
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._cached = None
        self._expires = 0

    def _gpu(self):
        """只有固定 NVIDIA 查询白名单，缺工具时降级。 Use the fixed NVIDIA query, degrading when unavailable."""
        executable = shutil.which('nvidia-smi')
        if not executable:
            return {'available': False, 'devices': [], 'warning': 'tool_not_found'}
        try:
            return _gpu_result(_capture([executable, '--query-gpu=' + _GPU_FIELDS,
                                        '--format=csv,noheader,nounits'], self.timeout_seconds))
        except _ProbeError as exc:
            return {'available': False, 'devices': [], 'warning': str(exc)}

    def _memory(self):
        """Windows 普通用户 CIM 查询，不收集序列号或主机身份。 Query CIM without elevation or device identities."""
        empty = {'available': False, 'total_bytes': None, 'modules': []}
        if os.name != 'nt':
            return {**empty, 'warning': 'unsupported_platform'}
        executable = shutil.which('powershell') or shutil.which('pwsh')
        if not executable:
            return {**empty, 'warning': 'tool_not_found'}
        try:
            return _memory_result(_capture([executable, '-NoLogo', '-NoProfile', '-NonInteractive',
                                           '-Command', _MEMORY_QUERY], self.timeout_seconds))
        except _ProbeError as exc:
            return {**empty, 'warning': str(exc)}

    def snapshot(self):
        """独立返回只读监控数据，调用方不能修改缓存。 Return isolated host data without exposing mutable cache."""
        with self._lock:
            if self._cached is None or time.monotonic() >= self._expires:
                self._cached = {
                    'collected_at': datetime.now(timezone.utc).isoformat(),
                    'cache_seconds': self.cache_seconds,
                    'gpu': self._gpu(), 'memory': self._memory(),
                }
                self._expires = time.monotonic() + self.cache_seconds
            return copy.deepcopy(self._cached)
