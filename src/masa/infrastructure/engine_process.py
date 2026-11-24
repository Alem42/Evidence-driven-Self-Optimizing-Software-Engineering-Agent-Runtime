"""Own a local inference engine and its descendants for the manager's lifetime."""
import os
from pathlib import Path
import subprocess
import sys


def _windows_job():
    import ctypes
    from ctypes import wintypes

    class Limits(ctypes.Structure):
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, including the six IO counters.
        _fields_ = [
            ('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
            ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
            ('process_limit', wintypes.DWORD), ('affinity', ctypes.c_size_t),
            ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD),
            ('io', ctypes.c_uint64 * 6), ('memory_limits_and_peaks', ctypes.c_size_t * 4),
        ]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Limits(flags=0x2000)  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(job)
        raise error
    return kernel, job


def start_engine(command, cwd, env, log):
    options = dict(cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    if os.name != 'nt':
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True, **options), None

    import ctypes
    kernel, job = _windows_job()
    launcher = None
    try:
        # Gate the first child until the OS can guarantee that all its descendants
        # share the job. Use the base interpreter to avoid a venv redirector.
        launcher = subprocess.Popen([sys._base_executable, '-I', '-S', str(Path(__file__).resolve()), *command],
                                    stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW, **options)
        if not kernel.AssignProcessToJobObject(job, int(launcher._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        launcher.stdin.write(b'1')
        launcher.stdin.close()
        return launcher, lambda: kernel.CloseHandle(job)
    except BaseException:
        kernel.CloseHandle(job)
        if launcher is not None:
            launcher.stdin.close()
            launcher.wait(timeout=10)
        raise


if __name__ == '__main__':
    if sys.stdin.buffer.read(1) == b'1':
        raise SystemExit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL,
                                         creationflags=subprocess.CREATE_NO_WINDOW))
