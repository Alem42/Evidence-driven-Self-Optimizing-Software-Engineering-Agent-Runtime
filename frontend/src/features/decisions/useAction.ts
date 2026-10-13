import { useMutation, useQueryClient } from '@tanstack/react-query';

/** 同步动作（审批、重新验证等）：统一 busy/error，并在完成后刷新相关查询。 */
export function useAction<T = unknown>(fn: () => Promise<T>, onSuccess?: (r: T) => void) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['detail'] });
      qc.invalidateQueries({ queryKey: ['view'] });
      qc.invalidateQueries({ queryKey: ['projects'] });
      onSuccess?.(r);
    },
  });
  return { run: () => m.mutate(), busy: m.isPending, error: m.error as Error | null, reset: m.reset };
}
