//go:build windows

package runner

import (
	"fmt"
	"syscall"
	"unsafe"
)

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
	limits.LimitFlags = 0x2000 // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
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

func isReparse(path string) bool {
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return true
	}
	attrs, err := syscall.GetFileAttributes(p)
	return err != nil || attrs&0x400 != 0
}
