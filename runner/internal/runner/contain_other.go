//go:build !windows

package runner

import "fmt"

func ContainCurrentProcess() error {
	return fmt.Errorf("P0 process containment currently supports Windows only")
}

func isReparse(string) bool { return false }
