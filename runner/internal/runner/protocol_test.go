package runner

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestProtocolRejectsUntrustedFieldsAndLimits(t *testing.T) {
	good := `{"protocol_version":1,"request_id":"x","snapshot_id":"s","operation":"go_test","timeout_ms":50,"max_output_bytes":32}`
	if _, err := Decode([]byte(good)); err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{strings.Replace(good, `"go_test"`, `"shell"`, 1), strings.Replace(good, `50`, `0`, 1), good + `{}`, strings.Replace(good, `"timeout_ms":50`, `"command":"whoami","timeout_ms":50`, 1), strings.Repeat(" ", MaxRequestBytes+1)} {
		if _, err := Decode([]byte(bad)); err == nil {
			t.Errorf("accepted bad request: %.100s", bad)
		}
	}
}

func TestCappedOutputContinuesDraining(t *testing.T) {
	b := &capBuffer{limit: 3}
	for i := 0; i < 100; i++ {
		n, err := b.Write([]byte("abcdef"))
		if n != 6 || err != nil {
			t.Fatal(n, err)
		}
	}
	if b.buf.String() != "abc" || !b.truncated {
		t.Fatal("wrong bounded result")
	}
}

func TestEnvironmentDoesNotPassSecretsOrGoFlags(t *testing.T) {
	t.Setenv("MASA_TEST_SECRET", "hidden")
	t.Setenv("GOFLAGS", "-exec=malicious")
	for _, e := range cleanEnvironment(filepath.Join(t.TempDir(), "go.exe")) {
		if strings.Contains(e, "hidden") || strings.Contains(e, "malicious") {
			t.Fatal(e)
		}
	}
}

func TestMissingModuleRejected(t *testing.T) {
	r := Request{ProtocolVersion: 1, RequestID: "r", Operation: "go_test", SnapshotID: "s", TimeoutMS: 100, MaxOutputBytes: 1024}
	res := Execute(context.Background(), r, Config{t.TempDir(), filepath.Join(t.TempDir(), "go.exe")})
	if res.Status != "rejected" {
		t.Fatal(res)
	}
}

func TestWorkspaceLinkRejected(t *testing.T) {
	root := t.TempDir()
	target := t.TempDir()
	if err := os.Symlink(target, filepath.Join(root, "outside")); err != nil {
		t.Skip(err)
	}
	if err := validateWorkspace(root); err == nil {
		t.Fatal("accepted linked workspace")
	}
}
