import { describe, expect, it } from 'vitest';
import type { LiveCall, LiveFile } from '../api/types';
import { editingFile, fileState, overlayFiles, writingFile } from './live';

const call = (files: LiveFile[]): LiveCall => ({ step: 'project_developer', chars: 10, seconds: 1, seq: 1, truncated: false, files, tail: '' });
const f = (path: string, complete: boolean, kind: 'write' | 'edit' = 'write', content = 'x'): LiveFile => ({ path, content, complete, kind });

describe('writingFile', () => {
  it('prefers the last unfinished file, else the last file', () => {
    expect(writingFile(call([f('a.go', true), f('b.go', false), f('c.go', false)]))?.path).toBe('c.go');
    expect(writingFile(call([f('a.go', true), f('b.go', true)]))?.path).toBe('b.go');
    expect(writingFile(call([]))).toBeUndefined();
    expect(writingFile(null)).toBeUndefined();
  });

  it('ignores edit fragments', () => {
    expect(writingFile(call([f('a.go', false, 'edit')]))).toBeUndefined();
    expect(editingFile(call([f('a.go', false, 'edit')]))?.path).toBe('a.go');
  });
});

describe('overlayFiles', () => {
  it('lays whole files over the known ones and leaves edit fragments out', () => {
    const base = { 'a.go': 'old a', 'b.go': 'old b' };
    const out = overlayFiles(base, call([f('a.go', false, 'write', 'new a'), f('b.go', false, 'edit', 'frag'), f('c.go', true, 'write', 'c')]));
    expect(out).toEqual({ 'a.go': 'new a', 'b.go': 'old b', 'c.go': 'c' });
    expect(base['a.go']).toBe('old a'); // 不改入参 / the input is not mutated
  });

  it('works without a base or a live call', () => {
    expect(overlayFiles(undefined, null)).toEqual({});
  });
});

describe('fileState', () => {
  const changed = new Set(['m.go']);
  it('ranks writing over editing over written over changed over sealed', () => {
    const live = call([f('a.go', false), f('b.go', false, 'edit'), f('c.go', true)]);
    expect(fileState('a.go', live, changed)).toBe('writing');
    expect(fileState('b.go', live, changed)).toBe('editing');
    expect(fileState('c.go', live, changed)).toBe('written');
    expect(fileState('m.go', live, changed)).toBe('changed');
    expect(fileState('z.go', live, changed)).toBe('sealed');
    expect(fileState('a.go', null, changed)).toBe('sealed');
  });
});
