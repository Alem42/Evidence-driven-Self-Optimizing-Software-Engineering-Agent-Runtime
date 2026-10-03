import { describe, expect, it } from 'vitest';
import { fmtDuration, fmtTokens, isTerminal } from './report';

describe('report helpers', () => {
  it('keeps unknown tokens unknown instead of zero', () => {
    expect(fmtTokens(null)).toBe('—');
    expect(fmtTokens(0)).toBe('0');
    expect(fmtTokens(12345)).toBe('12.3k');
    expect(fmtTokens(2_500_000)).toBe('2.50M');
  });
  it('formats durations', () => {
    expect(fmtDuration(null)).toBe('—');
    expect(fmtDuration(250)).toBe('250 ms');
    expect(fmtDuration(4500)).toBe('4.5 秒');
    expect(fmtDuration(125_000)).toBe('2 分 5 秒');
  });
  it('only terminal outcomes pop up', () => {
    expect(isTerminal('succeeded')).toBe(true);
    expect(isTerminal('failed')).toBe(true);
    expect(isTerminal('waiting')).toBe(false);
    expect(isTerminal('running')).toBe(false);
  });
});
