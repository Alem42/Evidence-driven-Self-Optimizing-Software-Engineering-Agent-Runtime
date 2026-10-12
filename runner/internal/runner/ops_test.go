package runner

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeOps(t *testing.T, body string) string {
	path := filepath.Join(t.TempDir(), "ops.json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestOpsTableRejectsUnsafeTables(t *testing.T) {
	exe := filepath.ToSlash(os.Args[0])
	good := `{"version":1,"root_marker":"marker.txt","env":{"allow":[],"set":{},"path_prepend":[]},"operations":{"t":{"exe":"` + exe + `","args":[]}}}`
	if _, err := LoadOps(writeOps(t, good)); err != nil {
		t.Fatal(err)
	}
	for name, bad := range map[string]string{
		"relative exe":    strings.Replace(good, exe, "go", 1),
		"unknown field":   strings.Replace(good, `"version":1`, `"version":1,"shell":"sh"`, 1),
		"wrong version":   strings.Replace(good, `"version":1`, `"version":2`, 1),
		"escaping root":   strings.Replace(good, "marker.txt", "../marker.txt", 1),
		"no operations":   `{"version":1,"root_marker":"m","env":{},"operations":{}}`,
		"unknown builtin": strings.Replace(good, `"exe":"`+exe+`","args":[]`, `"builtin":"rm"`, 1),
	} {
		if _, err := LoadOps(writeOps(t, bad)); err == nil {
			t.Errorf("accepted %s", name)
		}
	}
}

func TestRequestsOutsideTheTableAreRejected(t *testing.T) {
	ops := &Ops{Version: 1, RootMarker: "marker.txt", Operations: map[string]OpDef{"t": {Command: Command{Exe: os.Args[0]}}}}
	root := t.TempDir()
	_ = os.WriteFile(filepath.Join(root, "marker.txt"), nil, 0o600)
	base := Request{ProtocolVersion: 1, RequestID: "r", SnapshotID: "s", TimeoutMS: 5000, MaxOutputBytes: 4096}
	other := base
	other.Operation = "go_test" // 在 Go 清单里有、在这张表里没有 / exists in the Go table, not in this one
	if res := Execute(context.Background(), other, Config{Workspace: root, Ops: ops}); res.Status != "rejected" {
		t.Fatal(res)
	}
	withArgv := base
	withArgv.Operation = "t"
	withArgv.Argv = []string{"x"} // 这个操作不接受 argv / this operation takes no argv
	if res := Execute(context.Background(), withArgv, Config{Workspace: root, Ops: ops}); res.Status != "rejected" {
		t.Fatal(res)
	}
}

func TestATableOperationRunsInTheWorkspaceWithTheCleanEnvironment(t *testing.T) {
	t.Setenv("MASA_TEST_SECRET", "hidden")
	ops := &Ops{Version: 1, RootMarker: "marker.txt", Env: EnvPolicy{Set: map[string]string{"MASA_TEST_HELPER": "1"}},
		Operations: map[string]OpDef{"t": {Command: Command{Exe: os.Args[0], Args: []string{"-test.run=TestHelperPrintsEnvironment", "-test.v"}}}}}
	root := t.TempDir()
	_ = os.WriteFile(filepath.Join(root, "marker.txt"), nil, 0o600)
	req := Request{ProtocolVersion: 1, RequestID: "r", SnapshotID: "s", Operation: "t", TimeoutMS: 20000, MaxOutputBytes: 65536}
	res := Execute(context.Background(), req, Config{Workspace: root, Ops: ops})
	if res.Status != "completed" || res.ExitCode == nil || *res.ExitCode != 0 {
		t.Fatalf("%+v", res)
	}
	out := res.Stdout + res.Stderr
	if !strings.Contains(out, "helper-saw-flag") || strings.Contains(out, "hidden") {
		t.Fatal(out)
	}
}

// TestHelperPrintsEnvironment 只在被上面的测试当子进程运行时才有输出。 Prints something only when run as a child by the test above.
func TestHelperPrintsEnvironment(t *testing.T) {
	if os.Getenv("MASA_TEST_HELPER") != "1" {
		return
	}
	println("helper-saw-flag")
	if os.Getenv("MASA_TEST_SECRET") != "" {
		println("hidden")
	}
}
