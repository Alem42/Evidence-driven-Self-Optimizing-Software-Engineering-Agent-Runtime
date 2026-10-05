import { describe, expect, it } from 'vitest';
import type { Detail } from '../api/types';
import { isLocalRoute, modelsByRole, modelText } from './models';

const ev = (seq: number, role: string, route: Record<string, unknown>) => ({ seq, type: 'model_requested', created: seq, payload: { step_id: role, route } });
const LOCAL = { provider: 'ollama-native', model: 'glm-4.7-flash:latest', base_url: 'http://127.0.0.1:11434' };
const CLOUD = { provider: 'openai-compatible', model: 'deepseek-v4-pro', base_url: 'https://api.deepseek.com' };

describe('modelsByRole', () => {
  it('shows the model that really ran each role, in order, without adjacent duplicates', () => {
    const detail = { events: [ev(1, 'project_planner', CLOUD), ev(2, 'project_tester', CLOUD), ev(3, 'project_developer', LOCAL), ev(4, 'project_developer', LOCAL), ev(5, 'project_developer', CLOUD)] } as unknown as Detail;
    const ran = modelsByRole(detail);
    expect(modelText(ran.project_planner)).toBe('API deepseek-v4-pro');
    expect(modelText(ran.project_developer)).toBe('本地 glm-4.7-flash:latest → API deepseek-v4-pro');
  });

  it('classifies local routes by provider or loopback address', () => {
    expect(isLocalRoute(LOCAL)).toBe(true);
    expect(isLocalRoute({ base_url: 'http://localhost:11434' })).toBe(true);
    expect(isLocalRoute(CLOUD)).toBe(false);
  });
});
