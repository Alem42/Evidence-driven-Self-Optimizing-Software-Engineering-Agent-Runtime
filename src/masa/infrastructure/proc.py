"""子进程不弹控制台窗口（Windows）。Keep child processes from opening console windows on Windows."""
import os
import subprocess

# 从无控制台的父进程（桌面启动、后台服务）起 console 程序会每个弹一个 cmd 窗口。
# A console-less parent spawning console programs pops one cmd window per child.
NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

# POSIX：给子进程单独的会话（它成为进程组组长），结束时可以整组 kill。Windows 用 Job Object，不需要。
# POSIX: give the child its own session (it becomes a process-group leader) so the whole group can be killed; Windows uses a Job Object and needs nothing.
NEW_SESSION = {} if os.name == 'nt' else {'start_new_session': True}


def kill_group(pid):
    """POSIX：SIGKILL 整个进程组（组长已退出时组仍然有效，只要还有成员）；Windows 上什么都不做。
    POSIX: SIGKILL the whole process group (it stays addressable while any member lives); a no-op on Windows."""
    if os.name == 'nt':
        return
    import signal
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
