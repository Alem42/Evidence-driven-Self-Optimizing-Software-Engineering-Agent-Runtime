package runner

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type Config struct{ Workspace, GoExecutable string }

func validateWorkspace(root string) error {
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() {
		return fmt.Errorf("workspace is not a directory")
	}
	return filepath.WalkDir(root, func(path string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.Type()&os.ModeSymlink != 0 || isReparse(path) {
			return fmt.Errorf("link/reparse path denied: %s", path)
		}
		if !d.IsDir() && !d.Type().IsRegular() {
			return fmt.Errorf("non-regular workspace entry")
		}
		return nil
	})
}

// Only explicitly approved environment settings reach repository test code.
func cleanEnvironment(goExe string) []string {
	keys := []string{"SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "GOCACHE", "GOMODCACHE", "GOPATH", "GOROOT"}
	out := make([]string, 0, len(keys)+8)
	for _, key := range keys {
		if val := os.Getenv(key); val != "" {
			out = append(out, key+"="+val)
		}
	}
	path := filepath.Dir(goExe)
	if root := os.Getenv("SystemRoot"); root != "" {
		path += string(os.PathListSeparator) + filepath.Join(root, "System32")
	}
	out = append(out, "PATH="+path, "GOENV=off", "GOWORK=off", "GOTOOLCHAIN=local", "CGO_ENABLED=0", "GOPROXY=off", "GOFLAGS=-mod=readonly")
	return out
}

func Execute(ctx context.Context, req Request, cfg Config) Result {
	start := time.Now()
	if err := req.Validate(); err != nil {
		return Failure(req, "rejected", err)
	}
	if err := validateWorkspace(cfg.Workspace); err != nil {
		return Failure(req, "rejected", err)
	}
	if !filepath.IsAbs(cfg.GoExecutable) {
		return Failure(req, "rejected", fmt.Errorf("Go path must be absolute"))
	}
	if _, err := os.Stat(filepath.Join(cfg.Workspace, "go.mod")); err != nil {
		return Failure(req, "rejected", fmt.Errorf("go.mod is required"))
	}
	exe := cfg.GoExecutable
	var args []string
	switch req.Operation {
	case "go_test":
		args = []string{"test", "-json", "-count=1", fmt.Sprintf("-timeout=%dms", req.TimeoutMS), "./..."}
	case "go_vet":
		args = []string{"vet", "./..."}
	case "go_fmt_check":
		exe = filepath.Join(filepath.Dir(exe), "gofmt"+filepath.Ext(exe))
		args = []string{"-l", "."}
	}
	stdout := &capBuffer{limit: (req.MaxOutputBytes + 1) / 2}
	stderr := &capBuffer{limit: req.MaxOutputBytes / 2}
	cmd := exec.CommandContext(ctx, exe, args...)
	cmd.Dir, cmd.Env = cfg.Workspace, cleanEnvironment(cfg.GoExecutable)
	cmd.Stdout, cmd.Stderr = stdout, stderr
	// A descendant holding a pipe must not keep the worker alive indefinitely.
	cmd.WaitDelay = 300 * time.Millisecond
	err := cmd.Run()
	res := Result{ProtocolVersion: 1, RequestID: req.RequestID, SnapshotID: req.SnapshotID, Status: "completed", DurationMS: time.Since(start).Milliseconds(), Stdout: stdout.buf.String(), Stderr: stderr.buf.String(), Truncated: stdout.truncated || stderr.truncated}
	code := 0
	if err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			code = exit.ExitCode()
		} else {
			res.Status = "internal_error"
			res.Error = err.Error()
		}
	}
	if ctx.Err() != nil {
		res.Status = "cancelled"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Status = "timeout"
		}
		res.Error = ctx.Err().Error()
	} else if res.Status == "completed" {
		if req.Operation == "go_fmt_check" && strings.TrimSpace(res.Stdout) != "" {
			code = 1
		}
		res.ExitCode = &code
	}
	return res
}
