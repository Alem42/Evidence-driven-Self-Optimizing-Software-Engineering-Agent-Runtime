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
	ProtocolVersion int    `json:"protocol_version"`
	RequestID       string `json:"request_id"`
	Operation       string `json:"operation"`
	SnapshotID      string `json:"snapshot_id"`
	TimeoutMS       int    `json:"timeout_ms"`
	MaxOutputBytes  int    `json:"max_output_bytes"`
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
}

func Decode(data []byte) (Request, error) {
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
	return r, r.Validate()
}

func (r Request) Validate() error {
	if r.ProtocolVersion != 1 || r.RequestID == "" || len(r.RequestID) > 128 || r.SnapshotID == "" {
		return fmt.Errorf("invalid protocol, request ID, or snapshot")
	}
	if r.Operation != "go_test" && r.Operation != "go_vet" && r.Operation != "go_fmt_check" {
		return fmt.Errorf("operation is not allowed")
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
