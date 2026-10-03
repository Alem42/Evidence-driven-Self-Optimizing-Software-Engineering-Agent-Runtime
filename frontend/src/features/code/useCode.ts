import { useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { useArtifact } from '../../api/queries';
import type { Detail, SourceFinding } from '../../api/types';
import { useDrafts } from '../../stores/ui';

type Files = Record<string, string>;

/** 方案审批也有 approval_ref，但它不是源码引用。 Plan approvals are not source references. */
export function codeReference(data: Detail['run']['data']): string | null {
  const plan = data.project_plan;
  return data.project_bundle?.approval_ref || (plan?.kind === 'code' ? plan.approval_ref || plan.files_ref || null : null);
}

/** 当前版本的代码文件：原始内容 + 审核中的本地编辑 + 修复前基线。 */
export function useCodeFiles(detail: Detail) {
  const run = detail.run;
  const plan = run.data.project_plan;
  const ref = codeReference(run.data);
  const art = useArtifact<any>(run.id, ref);
  const base = useArtifact<any>(run.id, plan?.base_approval_ref);
  const draft = useDrafts((s) => s.files[run.id]);

  const original: Files | undefined = art.data ? ((art.data.files ?? art.data) as Files) : undefined;
  const editable = plan?.kind === 'code' && plan.status === 'awaiting_review' && run.status === 'paused' && !run.cancel_requested;
  const files = useMemo(() => draft ?? original, [draft, original]);

  /** 修复版本里被冻结的文件不可编辑。 Frozen files in repair revisions are read-only. */
  const locked = (path: string): boolean =>
    Boolean(plan?.repair_of) && (plan?.revision_scope === 'tests' ? !path.endsWith('_test.go') : path === 'go.mod' || path.endsWith('_test.go'));

  const changed = new Set(plan?.changed_files ?? []);
  return {
    loading: Boolean(ref) && art.isLoading,
    error: art.error as Error | null,
    hasRef: Boolean(ref),
    original,
    files,
    base: (base.data?.files ?? undefined) as Files | undefined,
    editable,
    dirty: Boolean(draft),
    locked,
    changed,
    isDraft: Boolean(plan),
  };
}

/** 源码语法与空测试检查结果；编辑后清除，避免展示旧版本结论。 */
export function useSourceReview(detail: Detail) {
  const qc = useQueryClient();
  const plan = detail.run.data.project_plan;
  const key = ['review', detail.run.id, plan?.files_ref ?? ''];
  const query = useQuery({
    queryKey: key,
    enabled: plan?.kind === 'code',
    staleTime: Infinity,
    queryFn: async (): Promise<{ findings: SourceFinding[] } | null> => {
      const event = detail.events.filter((e) => e.type === 'source_review_report' && e.payload.input_ref === plan?.files_ref).at(-1);
      if (!event) return null;
      return (await api<{ artifact: { findings: SourceFinding[] } }>('/runs/' + detail.run.id + '/artifacts/' + event.payload.report_ref)).artifact;
    },
  });
  return {
    report: query.data ?? null,
    set: (r: { findings: SourceFinding[] } | null) => qc.setQueryData(key, r),
  };
}
