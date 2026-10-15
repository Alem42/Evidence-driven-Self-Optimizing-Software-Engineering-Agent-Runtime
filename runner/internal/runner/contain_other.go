//go:build !windows

package runner

import (
	"fmt"
	"os"
	"os/exec"
	"runtime"
)

// ContainCurrentProcess：Windows 用 Job Object（kill-on-close）；Linux 的等价物是“父进程给 runner 单独的会话/进程组，结束时整组 kill”，
// 由 Python 侧（infrastructure/runner.py）负责，所以这里不需要在进程内做任何事。其它系统（macOS 等）仍然不支持。
// On Windows a kill-on-close Job Object contains the tree. The Linux equivalent is a dedicated session/process group owned by the parent, which is killed as a whole when the
// request ends; the Python side (infrastructure/runner.py) does that, so nothing is needed in-process. Other systems (macOS...) stay unsupported.
func ContainCurrentProcess() error {
	if runtime.GOOS != "linux" {
		return fmt.Errorf("process containment supports Windows and Linux only")
	}
	return nil
}

func HideWindow(*exec.Cmd) {}

// isReparse：Linux 上的“重解析点”就是符号链接。工作区里出现符号链接必须拒绝（它可以指向工作区之外）；Lstat 失败也按拒绝处理，和 Windows 版一致。
// On Linux a "reparse point" is a symlink. A symlink inside the workspace must be rejected (it can point outside); an Lstat failure is rejected too, like the Windows version.
func isReparse(path string) bool {
	info, err := os.Lstat(path)
	return err != nil || info.Mode()&os.ModeSymlink != 0
}
