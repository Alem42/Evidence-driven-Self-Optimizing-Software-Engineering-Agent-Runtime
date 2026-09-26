package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"time"

	"masa.local/runner/internal/runner"
)

func emit(result runner.Result) { _ = json.NewEncoder(os.Stdout).Encode(result) }

func main() {
	workspace := flag.String("workspace", "", "trusted workspace root")
	goExe := flag.String("go", "", "trusted absolute Go executable")
	worker := flag.Bool("worker", false, "internal contained worker")
	version := flag.Bool("version", false, "print version")
	flag.Parse()
	if *version {
		fmt.Println("masa-runner 0.1.0 protocol=1")
		return
	}
	input := bufio.NewReaderSize(os.Stdin, runner.MaxRequestBytes+1)
	line, err := input.ReadSlice('\n')
	if err != nil && !(errors.Is(err, io.EOF) && len(line) > 0) {
		emit(runner.Failure(runner.Request{}, "rejected", err))
		return
	}
	req, err := runner.Decode(line)
	if err != nil {
		emit(runner.Failure(req, "rejected", err))
		return
	}
	// ReadSlice aliases the reader buffer. The cancellation goroutine must not
	// overwrite the request while the child is still reading it.
	line = bytes.Clone(line)
	if err := runner.ContainCurrentProcess(); err != nil {
		emit(runner.Failure(req, "internal_error", err))
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(req.TimeoutMS)*time.Millisecond)
	defer cancel()
	if *worker {
		emit(runner.Execute(ctx, req, runner.Config{Workspace: *workspace, GoExecutable: *goExe}))
		return
	}
	// stdin remains open while executing. A cancel line or EOF cancels the job.
	go func() {
		line, err := input.ReadSlice('\n')
		if err != nil {
			cancel()
			return
		}
		var message struct {
			Cancel string `json:"cancel"`
		}
		if json.Unmarshal(line, &message) == nil && message.Cancel == req.RequestID {
			cancel()
		}
	}()
	exe, err := os.Executable()
	if err != nil {
		emit(runner.Failure(req, "internal_error", err))
		return
	}
	cmd := exec.CommandContext(ctx, exe, "--worker", "--workspace", *workspace, "--go", *goExe)
	cmd.Stdin = bytes.NewReader(line)
	var out, diagnostic bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &diagnostic
	cmd.WaitDelay = time.Second
	start := time.Now()
	err = cmd.Run()
	if ctx.Err() != nil {
		status := "cancelled"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			status = "timeout"
		}
		res := runner.Failure(req, status, ctx.Err())
		res.DurationMS = time.Since(start).Milliseconds()
		emit(res)
		return
	}
	var result runner.Result
	if err != nil || json.Unmarshal(out.Bytes(), &result) != nil {
		emit(runner.Failure(req, "internal_error", fmt.Errorf("worker failed: %v", err)))
		return
	}
	emit(result)
}
