package indexer

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

// TestFacts 检查同名方法、匿名函数与未知调用。 TestFacts checks receiver identity, closures and unresolved calls.
func TestFacts(t *testing.T) {
	raw := []byte("package demo\nimport alias \"strings\"\ntype A struct{}\ntype B struct{}\nfunc(a A) Run(){alias.TrimSpace(\"x\"); f:=func(){a.Run()}; f()}\nfunc(b *B) Run(){}\n")
	file := Parse("demo.go", raw)
	if len(file.Diagnostics) != 0 || len(file.Imports) != 1 || file.Imports[0].Alias != "alias" {
		t.Fatalf("bad facts: %+v", file)
	}
	keys := map[string]bool{}
	receivers := map[string]bool{}
	anonymous := false
	for _, s := range file.Symbols {
		if keys[s.Key] {
			t.Fatal("duplicate key")
		}
		keys[s.Key] = true
		if s.Name == "Run" {
			receivers[s.Receiver] = true
		}
		if s.Kind == "anonymous" {
			anonymous = true
		}
		if s.Start < 0 || s.End > len(raw) || s.Start >= s.End {
			t.Fatal("invalid range")
		}
	}
	if !receivers["A"] || !receivers["*B"] || !anonymous {
		t.Fatalf("missing identities: %+v", file.Symbols)
	}
	for _, c := range file.Calls {
		if c.Resolution != "unresolved_syntax" {
			t.Fatal("claimed resolved call")
		}
	}
}

// TestPartialAndLimits 验证解析失败、目录排除和取消。 TestPartialAndLimits verifies diagnostics, exclusions, and cancellation.
func TestPartialAndLimits(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "bad.go"), []byte("package demo\nfunc Broken( {"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(root, "vendor"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "vendor", "skip.go"), []byte("bad"), 0600); err != nil {
		t.Fatal(err)
	}
	index, err := Scan(context.Background(), root)
	if err != nil || !index.Partial || len(index.Files) != 1 {
		t.Fatalf("unexpected: %+v %v", index, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := Scan(ctx, root); err == nil {
		t.Fatal("cancel ignored")
	}
}
