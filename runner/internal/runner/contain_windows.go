//go:build windows

package runner

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"syscall"
	"unsafe"
)

// 作业的资源上限：被验证的代码失控（吃光内存、fork 炸弹）时，是它被系统结束并以非零退出码报告，而不是拖垮整台机器。
// 默认值对 go build/test、unittest 绰绰有余；环境变量可以调（测试里用来把上限压到很小）。
// Resource limits of the job: when the verified code runs away (eats all memory, fork bomb) it is ended by the system and reported with a non-zero exit code instead of taking the machine down.
// The defaults are ample for go build/test and unittest; environment variables can change them (the test squeezes the limit).
const (
	defaultJobMemoryMB   = 8192
	defaultActiveProcess = 512
)

func envInt(name string, fallback int) int {
	if v, err := strconv.Atoi(os.Getenv(name)); err == nil && v > 0 {
		return v
	}
	return fallback
}

// A worker joins a kill-on-close job BEFORE starting repository code.
// The process intentionally owns the handle for its entire lifetime. OS process
// teardown closes it and kills remaining descendants, including on abrupt death.
// The coordinator also joins a job so killing it cannot orphan its worker.
func ContainCurrentProcess() error {
	dll := syscall.NewLazyDLL("kernel32.dll")
	h, _, err := dll.NewProc("CreateJobObjectW").Call(0, 0)
	if h == 0 {
		return fmt.Errorf("CreateJobObject: %w", err)
	}
	var limits struct {
		PerProcessUserTimeLimit int64
		PerJobUserTimeLimit     int64
		LimitFlags              uint32
		MinimumWorkingSetSize   uintptr
		MaximumWorkingSetSize   uintptr
		ActiveProcessLimit      uint32
		Affinity                uintptr
		PriorityClass           uint32
		SchedulingClass         uint32
		IOCounters              [6]uint64
		ProcessMemoryLimit      uintptr
		JobMemoryLimit          uintptr
		PeakProcessMemoryUsed   uintptr
		PeakJobMemoryUsed       uintptr
	}
	// KILL_ON_JOB_CLOSE | ACTIVE_PROCESS | JOB_MEMORY
	limits.LimitFlags = 0x2000 | 0x0008 | 0x0200
	limits.ActiveProcessLimit = uint32(envInt("MASA_RUNNER_PROCESSES", defaultActiveProcess))
	limits.JobMemoryLimit = uintptr(envInt("MASA_RUNNER_MEMORY_MB", defaultJobMemoryMB)) << 20
	ok, _, err := dll.NewProc("SetInformationJobObject").Call(h, 9, uintptr(unsafe.Pointer(&limits)), unsafe.Sizeof(limits))
	if ok == 0 {
		syscall.CloseHandle(syscall.Handle(h))
		return fmt.Errorf("SetInformationJobObject: %w", err)
	}
	current, _, _ := dll.NewProc("GetCurrentProcess").Call()
	ok, _, err = dll.NewProc("AssignProcessToJobObject").Call(h, current)
	if ok == 0 {
		syscall.CloseHandle(syscall.Handle(h))
		return fmt.Errorf("AssignProcessToJobObject: %w", err)
	}
	return nil
}

// HideWindow 让子进程不弹出控制台窗口（go build/test/vet、被验证的程序）。
// HideWindow keeps child processes from flashing console windows (go build/test/vet, the verified program).
func HideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}

func isReparse(path string) bool {
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return true
	}
	attrs, err := syscall.GetFileAttributes(p)
	return err != nil || attrs&0x400 != 0
}
