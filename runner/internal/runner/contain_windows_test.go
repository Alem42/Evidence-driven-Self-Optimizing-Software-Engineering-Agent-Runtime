//go:build windows

package runner

import (
	"os"
	"os/exec"
	"testing"
)

// 子进程模式：先加入带上限的作业，再不停地申请并写入内存。上限生效时它会被结束（非零退出码），没有上限才会正常跑完。
// Child mode: join the limited job, then allocate and touch memory without stopping. With the limit it is ended (non-zero exit); without it the loop would finish.
func TestMain(m *testing.M) {
	if os.Getenv("MASA_TEST_HOG") == "1" {
		if err := ContainCurrentProcess(); err != nil {
			os.Exit(3)
		}
		var keep [][]byte
		for i := 0; i < 64; i++ { // 64 × 16 MB = 1 GB
			block := make([]byte, 16<<20)
			for j := 0; j < len(block); j += 4096 {
				block[j] = 1
			}
			keep = append(keep, block)
		}
		_ = keep
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func TestJobMemoryLimitEndsARunawayProcess(t *testing.T) {
	run := func(limitMB string) error {
		cmd := exec.Command(os.Args[0])
		cmd.Env = append(os.Environ(), "MASA_TEST_HOG=1", "MASA_RUNNER_MEMORY_MB="+limitMB)
		return cmd.Run()
	}
	if err := run("4096"); err != nil {
		t.Fatalf("a 1 GB allocation must fit a 4096 MB job: %v", err)
	}
	if err := run("64"); err == nil {
		t.Fatal("a 1 GB allocation survived a 64 MB job limit: the limit is not applied")
	}
}
