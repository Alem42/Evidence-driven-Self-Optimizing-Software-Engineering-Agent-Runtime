"""孤儿模型进程：Ollama 的 `llama-server.exe` 在父进程 `ollama serve` 被杀后可能继续存活，持续占着显存和内存。
这里只做两件事：检测（只读）和清理——且只清理“确实是孤儿的 Ollama llama-server”，别的进程一律不碰。
Orphaned model runners: Ollama's `llama-server.exe` can outlive its parent `ollama serve` after it is killed and keep holding VRAM and RAM.
This module only detects (read-only) and cleans up processes that are provably orphaned Ollama runners; nothing else is ever touched.

只支持 Windows（本项目的目标环境）；其它系统返回空列表。 Windows only; other systems report nothing.
"""
import json
import os
import subprocess
from pathlib import Path

from masa.domain.models import MasaError

_QUERY = (
    "$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
    "$ids=@{};Get-CimInstance Win32_Process | ForEach-Object { $ids[[int]$_.ProcessId]=$true };"
    "@(Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | ForEach-Object {"
    "$p=Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue;"
    "@{pid=[int]$_.ProcessId;parent_pid=[int]$_.ParentProcessId;parent_alive=[bool]$ids.ContainsKey([int]$_.ParentProcessId);"
    "path=$_.ExecutablePath;cmd=$_.CommandLine;ws_mb=[math]::Round($p.WorkingSet64/1MB);started=$p.StartTime.ToString('o')}"
    "}) | ConvertTo-Json -Depth 3 -Compress"
)
_LIMIT = 65536


def _ollama_dir() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama'


def _run(command, timeout=15) -> str:
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout, shell=False, stdin=subprocess.DEVNULL, **options)
    except (OSError, subprocess.TimeoutExpired):
        raise MasaError('无法查询进程信息') from None
    return done.stdout[:_LIMIT].decode('utf-8', 'replace')


def _model_blob(cmd: str) -> str | None:
    marker = '--model '
    if marker not in (cmd or ''):
        return None
    blob = cmd.split(marker, 1)[1].split(' ', 1)[0].strip('"')
    return Path(blob).name[:24] if blob else None


def list_runners() -> list[dict]:
    """所有 Ollama 的 llama-server 进程，并标明父进程是否还活着。 Every Ollama llama-server with whether its parent is alive."""
    if os.name != 'nt':
        return []
    raw = _run(['powershell', '-NoProfile', '-NonInteractive', '-Command', _QUERY]).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        raise MasaError('进程信息格式异常') from None
    data = [data] if isinstance(data, dict) else data
    root = str(_ollama_dir()).lower()
    out = []
    for item in data:
        path = str(item.get('path') or '').lower()
        if not path.startswith(root):  # 只关心 Ollama 自带的运行进程 / only Ollama's own runners
            continue
        out.append({'pid': int(item['pid']), 'parent_pid': int(item['parent_pid']), 'orphan': not item['parent_alive'],
                    'memory_mb': item.get('ws_mb'), 'started': item.get('started'), 'model_blob': _model_blob(item.get('cmd'))})
    return out


def orphans() -> list[dict]:
    return [r for r in list_runners() if r['orphan']]


def clean_orphans() -> dict:
    """清理孤儿运行进程。每个 pid 都要在清理瞬间重新确认仍是“父进程已不在的 Ollama llama-server”才会被终止。
    Terminate orphaned runners. Each pid is re-verified at kill time as an Ollama llama-server whose parent is gone."""
    killed, skipped, freed = [], [], 0
    for item in orphans():
        current = {r['pid']: r for r in list_runners()}.get(item['pid'])
        if not current or not current['orphan']:
            skipped.append(item['pid'])
            continue
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        done = subprocess.run(['taskkill', '/F', '/PID', str(item['pid'])], capture_output=True, timeout=15, shell=False,
                              stdin=subprocess.DEVNULL, **options)
        if done.returncode == 0:
            killed.append(item['pid'])
            freed += item['memory_mb'] or 0
        else:
            skipped.append(item['pid'])
    return {'killed': killed, 'skipped': skipped, 'freed_memory_mb': freed}
