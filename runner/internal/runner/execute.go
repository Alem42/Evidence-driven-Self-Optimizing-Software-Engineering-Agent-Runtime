package runner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"masa.local/runner/internal/indexer"
)

// Config：工作区与操作清单。清单由受信任的 Python 侧给出（--ops），或由 --go 生成。 The workspace and the operation table (from --ops, or generated from --go).
type Config struct {
	Workspace string
	Ops       *Ops
}

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

// expand 把参数模板里的 {timeout_ms} 与 {out} 换成具体值。 Fill {timeout_ms} and {out} in an argument template.
func expand(args []string, req Request, out string) []string {
	result := make([]string, len(args))
	for i, a := range args {
		a = strings.ReplaceAll(a, "{timeout_ms}", fmt.Sprint(req.TimeoutMS))
		result[i] = strings.ReplaceAll(a, "{out}", out)
	}
	return result
}

func Execute(ctx context.Context, req Request, cfg Config) Result {
	start := time.Now()
	ops := cfg.Ops
	if ops == nil {
		return Failure(req, "rejected", fmt.Errorf("no operation table"))
	}
	if err := req.Validate(ops.Policy()); err != nil {
		return Failure(req, "rejected", err)
	}
	if err := validateWorkspace(cfg.Workspace); err != nil {
		return Failure(req, "rejected", err)
	}
	if _, err := os.Stat(filepath.Join(cfg.Workspace, ops.RootMarker)); err != nil {
		return Failure(req, "rejected", fmt.Errorf("%s is required", ops.RootMarker))
	}
	op := ops.Operations[req.Operation]
	// 索引复用进程隔离与时限，但不启动编译器。 Indexing reuses containment and deadlines without compilation.
	if op.Builtin == "go_index" {
		index, err := indexer.Scan(ctx, cfg.Workspace)
		if err != nil {
			return Failure(req, "internal_error", err)
		}
		raw, err := json.Marshal(index)
		if err != nil {
			return Failure(req, "internal_error", err)
		}
		// 不发布截断 JSON，调用方必须知道没有完整 generation。
		// Never publish truncated JSON as an index generation.
		if len(raw) > req.MaxOutputBytes {
			return Failure(req, "rejected", fmt.Errorf("index output limit exceeded"))
		}
		code := 0
		return Result{ProtocolVersion: 1, RequestID: req.RequestID, SnapshotID: req.SnapshotID, Status: "completed", ExitCode: &code, Stdout: string(raw), DurationMS: time.Since(start).Milliseconds()}
	}
	env := ops.environment()
	exe, args := op.Exe, expand(op.Args, req, "")
	if op.Build != nil {
		// 构建产物放在临时目录，不改变批准快照；参数只传给程序。
		// Build outside the approved snapshot and pass argv only to the executable.
		dir, err := os.MkdirTemp("", "masa-app-")
		if err != nil {
			return Failure(req, "internal_error", err)
		}
		defer os.RemoveAll(dir)
		binary := filepath.Join(dir, "app"+filepath.Ext(op.Build.Exe))
		build := exec.CommandContext(ctx, op.Build.Exe, expand(op.Build.Args, req, binary)...)
		HideWindow(build)
		build.Dir, build.Env = cfg.Workspace, env
		output := &capBuffer{limit: req.MaxOutputBytes}
		build.Stdout, build.Stderr = output, output
		build.WaitDelay = 300 * time.Millisecond
		if err := build.Run(); err != nil {
			res := Failure(req, "internal_error", err)
			res.Phase = "build"
			res.Stderr = output.buf.String()
			res.Truncated = output.truncated
			var exit *exec.ExitError
			if errors.As(err, &exit) {
				code := exit.ExitCode()
				res.Status = "completed"
				res.ExitCode = &code
			}
			if ctx.Err() != nil {
				res.Status = "cancelled"
				if errors.Is(ctx.Err(), context.DeadlineExceeded) {
					res.Status = "timeout"
				}
				res.ExitCode = nil
			}
			return res
		}
		exe, args = binary, nil
	}
	if op.ArgvFromRequest {
		args = append(args, req.Argv...)
	}
	stdout := &capBuffer{limit: (req.MaxOutputBytes + 1) / 2}
	stderr := &capBuffer{limit: req.MaxOutputBytes / 2}
	cmd := exec.CommandContext(ctx, exe, args...)
	HideWindow(cmd)
	cmd.Dir, cmd.Env = cfg.Workspace, env
	cmd.Stdout, cmd.Stderr = stdout, stderr
	if op.ArgvFromRequest {
		cmd.Stdin = strings.NewReader(req.Stdin) // 没有就是空输入，而不是继承运行器的标准输入 / empty input when absent, never the runner's own stdin
	}
	// A descendant holding a pipe must not keep the worker alive indefinitely.
	cmd.WaitDelay = 300 * time.Millisecond
	err := cmd.Run()
	res := Result{ProtocolVersion: 1, RequestID: req.RequestID, SnapshotID: req.SnapshotID, Status: "completed", DurationMS: time.Since(start).Milliseconds(), Stdout: stdout.buf.String(), Stderr: stderr.buf.String(), Truncated: stdout.truncated || stderr.truncated}
	if op.Run {
		res.Phase = "run"
	}
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
		if op.FailIfStdoutNonEmpty && strings.TrimSpace(res.Stdout) != "" {
			code = 1
		}
		res.ExitCode = &code
	}
	return res
}
