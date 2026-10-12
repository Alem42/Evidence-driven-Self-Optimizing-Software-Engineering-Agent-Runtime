// 语言相关的界面小工具：测试文件判断、清单文件、操作名。具体语言的信息来自后端的语言包，这里只做通用判断。
// Small language-aware UI helpers: test-file detection, manifest files, operation names. Per-language facts come from the backend language packs; only generic rules live here.
import type { LanguageSummary } from '../api/types';

/** 测试文件：Go 的 _test.go、Python 的 tests/ 目录或 test_*.py。 Test files: Go _test.go, Python tests/ or test_*.py. */
export const isTestPath = (p: string): boolean => p.endsWith('_test.go') || /(^|\/)tests\//.test(p) || /(^|\/)test_[^/]*\.py$/.test(p);

/** 清单文件（由规格确定，不由模型写）。 Manifest files (fixed by the spec, never written by a model). */
export const isManifestPath = (p: string): boolean => p === 'go.mod' || p === 'pyproject.toml';

/** 入口文件：Go 的 cmd/app/main.go、Python 的 <模块>/__main__.py。 Entry files. */
export const isEntryPath = (p: string): boolean => p === 'cmd/app/main.go' || /^[a-z][a-z0-9_]*\/__main__\.py$/.test(p);

/** 源码文件（含测试）。 Source files, tests included. */
export const isCodePath = (p: string): boolean => p.endsWith('.go') || p.endsWith('.py');

/** 编辑器高亮用的语言：按文件后缀。 The highlight language by file extension. */
export const highlightOf = (p: string): 'go' | 'python' | 'plain' => (p.endsWith('.go') ? 'go' : p.endsWith('.py') ? 'python' : 'plain');

const OPERATION_LABELS: Record<string, string> = {
  go_test: 'go test',
  go_vet: 'go vet',
  go_fmt_check: 'gofmt 检查',
  app_run: '运行程序',
  go_index: '代码索引',
  py_test: 'python 测试',
  py_static: 'python 静态检查',
  py_run: '运行程序',
  gate: 'Gate 核对',
  run_application: '运行程序',
};
export const operationLabel = (op: string): string => OPERATION_LABELS[op] ?? op;

export const languageTitle = (languages: LanguageSummary[] | undefined, id: string | undefined): string => languages?.find((l) => l.id === id)?.title ?? id ?? 'Go';
