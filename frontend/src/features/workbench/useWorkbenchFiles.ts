import { useMemo } from 'react';
import { useArtifact } from '../../api/queries';
import type { Detail, LiveCall } from '../../api/types';
import { overlayFiles } from '../../entities/live';
import { useCodeFiles } from '../code/useCode';

type Files = Record<string, string>;

/** 工作台里能看到的全部文件：已封包的代码（含审核中的本地编辑）+ 逐个生成时已完成的部分 + 模型此刻正在写的那一个。
 *  Everything the workbench can show: the sealed code (with local review edits), the files finished so far while generating one by one, and the one the model is writing right now. */
export function useWorkbenchFiles(detail: Detail, live: LiveCall | null) {
  const code = useCodeFiles(detail);
  const partial = useArtifact<Files>(detail.run.id, code.hasRef ? undefined : detail.run.data.project_plan?.partial_files_ref);
  const files = useMemo(() => overlayFiles({ ...(partial.data ?? {}), ...(code.files ?? {}) }, live), [partial.data, code.files, live]);
  const paths = useMemo(() => Object.keys(files), [files]);
  return { code, files, paths, loading: code.loading && !paths.length };
}
