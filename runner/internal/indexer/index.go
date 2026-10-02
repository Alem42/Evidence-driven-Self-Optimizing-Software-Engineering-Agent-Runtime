// Package indexer 提取语法事实，不进行类型解析。 Package indexer extracts syntax facts without type resolution.
package indexer

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type Import struct {
	Path  string `json:"path"`
	Alias string `json:"alias"`
}
type Symbol struct {
	Key           string `json:"key"`
	Name          string `json:"name"`
	Kind          string `json:"kind"`
	Receiver      string `json:"receiver"`
	Signature     string `json:"signature"`
	Documentation string `json:"documentation"`
	Start         int    `json:"start"`
	End           int    `json:"end"`
	StartLine     int    `json:"start_line"`
	EndLine       int    `json:"end_line"`
	Test          bool   `json:"test"`
}
type Call struct {
	Target     string `json:"target"`
	Caller     string `json:"caller"`
	Line       int    `json:"line"`
	Resolution string `json:"resolution"`
}
type File struct {
	Path           string        `json:"path"`
	Hash           string        `json:"hash"`
	Package        string        `json:"package"`
	Imports        []Import      `json:"imports"`
	Symbols        []Symbol      `json:"symbols"`
	Calls          []Call        `json:"calls"`
	Diagnostics    []string      `json:"diagnostics"`
	BuildInclusion string        `json:"build_inclusion"`
	Generated      bool          `json:"generated"`
	TestFindings   []TestFinding `json:"test_findings"`
}

// TestFinding 保存测试结构问题，不把语法检查当作覆盖证明。 TestFinding records structural issues, not coverage proof.
type TestFinding struct {
	Code    string `json:"code"`
	Line    int    `json:"line"`
	Message string `json:"message"`
}
type Index struct {
	Schema      int      `json:"schema"`
	Version     string   `json:"version"`
	Files       []File   `json:"files"`
	Partial     bool     `json:"partial"`
	Limitations []string `json:"limitations"`
}

// text 格式化 AST 片段用于展示。 text formats an AST fragment for display.
func text(set *token.FileSet, node any) string {
	var out bytes.Buffer
	_ = printer.Fprint(&out, set, node)
	return out.String()
}

// Parse 提取单文件声明与调用候选，语法错误保留诊断。 Parse retains diagnostics and syntax candidates on parse errors.
func Parse(path string, raw []byte) File {
	set := token.NewFileSet()
	file, err := parser.ParseFile(set, path, raw, parser.ParseComments|parser.AllErrors|parser.SkipObjectResolution)
	result := File{Path: path, Hash: fmt.Sprintf("%x", sha256.Sum256(raw)), Imports: []Import{}, Symbols: []Symbol{}, Calls: []Call{}, Diagnostics: []string{}, BuildInclusion: "unknown"}
	if err != nil {
		result.Diagnostics = append(result.Diagnostics, err.Error())
	}
	if file == nil {
		return result
	}
	result.Package = file.Name.Name
	result.Generated = ast.IsGenerated(file)
	for _, imp := range file.Imports {
		name, _ := strconv.Unquote(imp.Path.Value)
		alias := ""
		if imp.Name != nil {
			alias = imp.Name.Name
		}
		result.Imports = append(result.Imports, Import{name, alias})
	}
	// key 包含文件和位置，避免同包同名或 build-tag 变体被错误合并。
	// Include file and offset in keys so duplicate names and build variants remain distinct.
	add := func(node ast.Node, name, kind, receiver, signature string) {
		start, end := set.Position(node.Pos()), set.Position(node.End())
		documentation := ""
		for _, group := range file.Comments {
			if set.Position(group.End()).Line == start.Line-1 {
				documentation = group.Text()
			}
		}
		result.Symbols = append(result.Symbols, Symbol{Key: fmt.Sprintf("%s::%s::%s::%s::%s@%d", path, result.Package, kind, receiver, name, start.Offset), Name: name, Kind: kind, Receiver: receiver, Signature: signature, Start: start.Offset, End: end.Offset, StartLine: start.Line, EndLine: end.Line, Test: strings.HasSuffix(path, "_test.go") && kind == "function" && (strings.HasPrefix(name, "Test") || strings.HasPrefix(name, "Benchmark") || strings.HasPrefix(name, "Fuzz") || strings.HasPrefix(name, "Example"))})
		result.Symbols[len(result.Symbols)-1].Documentation = documentation
	}
	for _, decl := range file.Decls {
		switch node := decl.(type) {
		case *ast.FuncDecl:
			receiver, kind := "", "function"
			if node.Recv != nil && len(node.Recv.List) > 0 {
				receiver = text(set, node.Recv.List[0].Type)
				kind = "method"
			}
			add(node, node.Name.Name, kind, receiver, text(set, node.Type))
			// 仅检测确切空测试函数；不根据函数名推断断言充分性。
			// Detect exactly empty test functions without guessing assertion sufficiency.
			if strings.HasSuffix(path, "_test.go") && strings.HasPrefix(node.Name.Name, "Test") && node.Body != nil && len(node.Body.List) == 0 {
				result.TestFindings = append(result.TestFindings, TestFinding{"empty_test", set.Position(node.Pos()).Line, "test function has an empty body"})
			}
		case *ast.GenDecl:
			for _, spec := range node.Specs {
				switch value := spec.(type) {
				case *ast.TypeSpec:
					add(value, value.Name.Name, "type", "", text(set, value))
				case *ast.ValueSpec:
					for _, name := range value.Names {
						add(value, name.Name, strings.ToLower(node.Tok.String()), "", text(set, value))
					}
				}
			}
		}
	}
	ast.Inspect(file, func(node ast.Node) bool {
		if literal, ok := node.(*ast.FuncLit); ok {
			add(literal, fmt.Sprintf("anonymous@%d", set.Position(literal.Pos()).Offset), "anonymous", "", text(set, literal.Type))
		}
		return true
	})
	ast.Inspect(file, func(node ast.Node) bool {
		if call, ok := node.(*ast.CallExpr); ok {
			caller, size := "", len(raw)+1
			position := set.Position(call.Pos())
			for _, symbol := range result.Symbols {
				if (symbol.Kind == "function" || symbol.Kind == "method" || symbol.Kind == "anonymous") && symbol.Start <= position.Offset && position.Offset < symbol.End && symbol.End-symbol.Start < size {
					caller = symbol.Key
					size = symbol.End - symbol.Start
				}
			}
			// 调用表达式也可能是类型转换；绝不声称已绑定真实目标。
			// A call expression may be a conversion; never claim a resolved target.
			result.Calls = append(result.Calls, Call{text(set, call.Fun), caller, position.Line, "unresolved_syntax"})
		}
		return true
	})
	return result
}

// Scan 有界遍历仓库，不运行生成器或加载依赖。 Scan walks a bounded repository without generators or dependency loading.
func Scan(ctx context.Context, root string) (Index, error) {
	result := Index{Schema: 1, Version: "go-ast-v1", Files: []File{}, Limitations: []string{"syntax only; no type binding or exact call graph", "build inclusion unknown; all included Go sources parsed", "test names are candidates, not coverage"}}
	total := 0
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if d.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("links denied")
		}
		if d.IsDir() {
			if path != root && (strings.HasPrefix(d.Name(), ".") || d.Name() == "vendor" || d.Name() == "node_modules") {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if info.Size() > 2*1024*1024 || total+int(info.Size()) > 32*1024*1024 || len(result.Files) >= 4000 {
			return fmt.Errorf("index input limit exceeded")
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		total += len(raw)
		relative, _ := filepath.Rel(root, path)
		file := Parse(filepath.ToSlash(relative), raw)
		if len(file.Diagnostics) > 0 {
			result.Partial = true
		}
		result.Files = append(result.Files, file)
		return nil
	})
	return result, err
}
