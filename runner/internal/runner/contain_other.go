//go:build !windows

package runner

import (
	"fmt"
	"os/exec"
)

func ContainCurrentProcess() error {
	return fmt.Errorf("P0 process containment currently supports Windows only")
}

func HideWindow(*exec.Cmd) {}

func isReparse(string) bool { return false }
