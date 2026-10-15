package runner

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"sync"
)

const MaxRequestBytes = 16384

type Request struct {
	ProtocolVersion int      `json:"protocol_version"`
	RequestID       string   `json:"request_id"`
	Operation       string   `json:"operation"`
	SnapshotID      string   `json:"snapshot_id"`
	TimeoutMS       int      `json:"timeout_ms"`
	MaxOutputBytes  int      `json:"max_output_bytes"`
	Argv            []string `json:"argv,omitempty"`
	// Stdin：只有运行类操作（带 argv 的操作）可以给标准输入，最多 8 KiB。 Standard input: only run-kind operations (those that take an argv) may carry it, 8 KiB at most.
	Stdin string `json:"stdin,omitempty"`
}

type Result struct {
	ProtocolVersion int    `json:"protocol_version"`
	RequestID       string `json:"request_id"`
	SnapshotID      string `json:"snapshot_id"`
	Status          string `json:"status"`
	ExitCode        *int   `json:"exit_code"`
	DurationMS      int64  `json:"duration_ms"`
	Stdout          string `json:"stdout"`
	Stderr          string `json:"stderr"`
	Truncated       bool   `json:"truncated"`
	Error           string `json:"error,omitempty"`
	Phase           string `json:"phase,omitempty"`
}

// Policy 是请求校验用的名单：允许哪些操作、哪些操作可以带 argv。 What a request may name: allowed operations, and which of them take an argv.
type Policy struct {
	Operations map[string]bool
	Argv       map[string]bool
}

// DefaultPolicy 是没有操作清单时的 Go 名单（与以前写死的一致）。 The Go lists used when there is no ops table (identical to the previous hard-coded ones).
func DefaultPolicy() Policy { return LegacyGoOps("/x").Policy() }

func Decode(data []byte, policy Policy) (Request, error) {
	var r Request
	if len(data) > MaxRequestBytes {
		return r, fmt.Errorf("request too large")
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(&r); err != nil {
		return r, err
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		return r, fmt.Errorf("trailing request data")
	}
	return r, r.Validate(policy)
}

func (r Request) Validate(policy Policy) error {
	if r.ProtocolVersion != 1 || r.RequestID == "" || len(r.RequestID) > 128 || r.SnapshotID == "" {
		return fmt.Errorf("invalid protocol, request ID, or snapshot")
	}
	if !policy.Operations[r.Operation] {
		return fmt.Errorf("operation is not allowed")
	}
	if len(r.Stdin) > 8192 || (!policy.Argv[r.Operation] && r.Stdin != "") {
		return fmt.Errorf("stdin outside runner policy")
	}
	if len(r.Argv) > 64 || (!policy.Argv[r.Operation] && len(r.Argv) > 0) {
		return fmt.Errorf("argv outside runner policy")
	}
	for _, arg := range r.Argv {
		if len(arg) > 4096 || bytes.IndexByte([]byte(arg), 0) >= 0 {
			return fmt.Errorf("invalid application argument")
		}
	}
	if r.TimeoutMS < 1 || r.TimeoutMS > 120000 || r.MaxOutputBytes < 1 || r.MaxOutputBytes > 1048576 {
		return fmt.Errorf("limits outside runner policy")
	}
	return nil
}

func Failure(r Request, status string, err error) Result {
	res := Result{ProtocolVersion: 1, RequestID: r.RequestID, SnapshotID: r.SnapshotID, Status: status}
	if err != nil {
		res.Error = err.Error()
	}
	return res
}

// capBuffer always drains writes, even when storage is exhausted.
type capBuffer struct {
	mu        sync.Mutex
	buf       bytes.Buffer
	limit     int
	truncated bool
}

func (b *capBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := len(p)
	left := b.limit - b.buf.Len()
	if len(p) > left {
		p = p[:left]
		b.truncated = true
	}
	_, _ = b.buf.Write(p)
	return n, nil
}
