import { describe, expect, it } from 'vitest';
import { byRouting, normalizeProfiles, parseProfile, profileLabel, profileReady } from './profiles';
import type { Profile } from '../api/types';

const p = (o: Partial<Profile>): Profile => ({ id: 'x', base_url: '', model: 'm', model_type: 'cloud', protocol: 'openai', ...o });

describe('profiles', () => {
  it('local models need no key, disabled ones are not ready', () => {
    expect(profileReady(p({ model_type: 'local' }))).toBe(true);
    expect(profileReady(p({ model_type: 'cloud' }))).toBe(false);
    expect(profileReady(p({ model_type: 'local', enabled: false }))).toBe(false);
  });
  it('labels include type and level', () => {
    expect(profileLabel(p({ model_type: 'local', name: 'Q' }))).toBe('Q · 本地 L1');
  });
  it('sorts by level then priority', () => {
    const list = [p({ id: 'c', level: 2 }), p({ id: 'b', level: 1, priority: 5 }), p({ id: 'a', level: 1, priority: 1 })];
    expect(list.sort(byRouting).map((x) => x.id)).toEqual(['a', 'b', 'c']);
  });
  it('legacy payloads normalise', () => {
    expect(normalizeProfiles({} as any).compatible).toBe(false);
  });
  it('imports aliases and rejects bad json', () => {
    expect(parseProfile('{"baseURL":"u","model":"m","apiKey":"k"}')).toMatchObject({ base_url: 'u', api_key: 'k' });
    expect(() => parseProfile('nope')).toThrow('JSON');
    expect(() => parseProfile('{"model":"m"}')).toThrow('base_url');
  });
});
