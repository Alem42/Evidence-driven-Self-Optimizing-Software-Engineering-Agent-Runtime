import { useState } from 'react';
import { useProfiles } from '../../api/queries';
import { profileLabel, profileReady } from '../../entities/profiles';

/** 模型选择状态：默认使用配置中的默认模型，用户改动后保持。 */
export function useModelChoice() {
  const { data } = useProfiles();
  const [chosen, setChosen] = useState('');
  const ready = (data?.profiles ?? []).filter(profileReady);
  const id = ready.find((p) => p.id === chosen)?.id || ready.find((p) => p.id === data?.active_id)?.id || ready[0]?.id || '';
  return { id, setId: setChosen, ready, any: ready.length > 0 };
}

export function ModelSelect({ choice, disabled }: { choice: ReturnType<typeof useModelChoice>; disabled?: boolean }) {
  return (
    <select aria-label="使用模型" disabled={disabled} value={choice.id} onChange={(e) => choice.setId(e.target.value)}>
      {!choice.any && <option value="">没有可用模型</option>}
      {choice.ready.map((p) => (
        <option key={p.id} value={p.id}>
          {profileLabel(p)}
        </option>
      ))}
    </select>
  );
}
