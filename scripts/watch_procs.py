"""进程创建监视器（Windows，无需管理员）：高频快照进程表，记录新出现的进程、父进程与是否拥有控制台。
Process-creation watcher (Windows, no admin): polls the process table at high frequency and logs new processes with their parent.
用法 / usage: python scripts/watch_procs.py <seconds> [out.log]"""
import ctypes
import ctypes.wintypes as w
import sys
import time

TH32CS_SNAPPROCESS = 0x2


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', w.DWORD), ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', w.LONG),
                ('dwFlags', w.DWORD), ('szExeFile', ctypes.c_char * 260)]


k = ctypes.windll.kernel32
k.CreateToolhelp32Snapshot.restype = w.HANDLE


def snapshot():
    h = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(entry)
    out = {}
    ok = k.Process32First(h, ctypes.byref(entry))
    while ok:
        out[entry.th32ProcessID] = (entry.szExeFile.decode('mbcs', 'replace'), entry.th32ParentProcessID)
        ok = k.Process32Next(h, ctypes.byref(entry))
    k.CloseHandle(h)
    return out


def main():
    seconds = float(sys.argv[1])
    log = open(sys.argv[2], 'w', encoding='utf-8') if len(sys.argv) > 2 else sys.stdout
    known = snapshot()
    names = dict(known)
    start = time.time()
    while time.time() - start < seconds:
        now = snapshot()
        for pid, (name, ppid) in now.items():
            if pid not in known:
                parent = names.get(ppid, now.get(ppid, ('?', 0)))[0]
                names[pid] = (name, ppid)
                log.write(f'{time.time() - start:7.2f}s  {name:<22} pid={pid:<7} parent={parent}({ppid})\n')
                log.flush()
        known = now
        time.sleep(0.005)


if __name__ == '__main__':
    main()
