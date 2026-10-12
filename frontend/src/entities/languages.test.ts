import { describe, expect, it } from 'vitest';
import { highlightOf, isEntryPath, isManifestPath, isTestPath, operationLabel } from './languages';
import { parseDiagnostics } from './text';

describe('languages', () => {
  it('tells test, manifest and entry files apart for Go and Python', () => {
    expect(isTestPath('internal/a/a_test.go')).toBe(true);
    expect(isTestPath('tests/test_app.py')).toBe(true);
    expect(isTestPath('calc/test_helpers.py')).toBe(true);
    expect(isTestPath('calc/app.py')).toBe(false);
    expect(isManifestPath('go.mod') && isManifestPath('pyproject.toml')).toBe(true);
    expect(isEntryPath('cmd/app/main.go') && isEntryPath('calc/__main__.py')).toBe(true);
    expect(isEntryPath('calc/app/__main__.py')).toBe(false);
  });
  it('picks highlighting by extension and labels operations of every language', () => {
    expect(highlightOf('a.py')).toBe('python');
    expect(highlightOf('a.go')).toBe('go');
    expect(highlightOf('pyproject.toml')).toBe('plain');
    expect(operationLabel('py_test')).toBe('python 测试');
    expect(operationLabel('go_vet')).toBe('go vet');
    expect(operationLabel('unknown_op')).toBe('unknown_op');
  });
  it('parses Python and Go diagnostics', () => {
    expect(parseDiagnostics('calc/app.py:3:5: SyntaxError: invalid syntax')).toEqual([{ path: 'calc/app.py', line: 3, message: 'SyntaxError: invalid syntax' }]);
  });
});
