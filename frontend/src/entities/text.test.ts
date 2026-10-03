import { describe, expect, it } from 'vitest';
import { diffLines, failureSummary, parseDiagnostics, readableOutput } from './text';

describe('text helpers', () => {
  it('turns go test json into readable lines', () => {
    const out = readableOutput('{"Action":"output","Package":"p","Output":"FAIL: boom\\n"}\n{"Action":"fail","Package":"p"}\nplain');
    expect(out).toBe('FAIL: boom\nplain');
  });
  it('summarises failures', () => {
    expect(failureSummary({ stderr: 'a\nundefined: x\nb' })).toContain('undefined: x');
  });
  it('parses compiler diagnostics', () => {
    const d = parseDiagnostics('./cmd/app/main.go:12:5: undefined: foo\nrandom line');
    expect(d).toEqual([{ path: 'cmd/app/main.go', line: 12, message: 'undefined: foo' }]);
  });
  it('diffs lines', () => {
    const rows = diffLines('a\nb\nc', 'a\nx\nc');
    expect(rows.map((r) => r.kind)).toEqual(['same', 'del', 'add', 'same']);
  });
});
