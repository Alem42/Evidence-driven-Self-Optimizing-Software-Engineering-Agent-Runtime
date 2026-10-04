"""子进程不弹控制台窗口（Windows）。Keep child processes from opening console windows on Windows."""
import os
import subprocess

# 从无控制台的父进程（桌面启动、后台服务）起 console 程序会每个弹一个 cmd 窗口。
# A console-less parent spawning console programs pops one cmd window per child.
NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
