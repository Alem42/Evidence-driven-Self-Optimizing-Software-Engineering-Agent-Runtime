// ───────── 评测 Benchmark ─────────
export interface BenchTask { id: string; level: number; title: string; goal: string; cases: number; probes: string[]; max_seconds: number; max_cloud_tokens: number }
export interface BenchSuite { title: string; desc: string; tasks: string[]; repeats: number; total_cloud_tokens: number; total_minutes: number }
export interface BenchCatalog { tasks: BenchTask[]; suites: Record<string, BenchSuite>; levels: Record<string, string> }
export type BenchStatus = 'pass' | 'false_pass' | 'gate_fail' | 'stopped' | 'timeout' | 'error' | 'skipped';
export interface BenchRecord {
  task: string; level: number; repeat: number; status: BenchStatus; gate_passed: boolean; oracle_passed: boolean | null; oracle_reason: string;
  seconds: number; calls: number; failed_calls: number; local_tokens: number; cloud_tokens: number; escalations: number; rounds: number;
  mechanisms: Record<string, number>; stop_reason: string | null; note: string | null; fail_stage: string | null;
}
export interface BenchRow {
  runs: number; passed: number; false_pass: number; rate: number; level: number;
  mean_cloud_tokens: number | null; mean_seconds: number | null; mean_calls: number | null; mean_rounds: number | null; mean_escalations: number | null;
  outcomes: Record<string, number>;
}
export interface BenchOverall {
  runs: number; skipped: number; passed: number; rate: number | null; false_pass: number; stable_level: number | null; ceiling_level: number | null;
  weighted_score: number | null; cloud_tokens: number; local_tokens: number; seconds: number; cloud_tokens_per_pass: number | null; seconds_per_pass: number | null;
  mechanisms: Record<string, number>;
}
export interface BenchAggregate { overall: BenchOverall; by_level: Record<string, BenchRow>; by_task: Record<string, BenchRow> }
export interface BenchConfig { suite: string; task_ids: string[]; repeats: number; total_cloud_tokens: number; total_minutes: number; per_run_scale: number }
export interface BenchResult {
  id: string; state: 'starting' | 'running' | 'done' | 'stopped' | 'error' | 'idle'; config: BenchConfig; started: number; total: number; message?: string | null;
  current: { task: string; level: number; repeat: number; index: number; started: number; stage?: string | null; model?: string | null; kind?: 'local' | 'cloud' | null; run_cloud_tokens?: number; live_chars?: number | null; limit_seconds?: number; limit_cloud_tokens?: number } | null;
  plan?: { task: string; level: number; repeat: number }[];
  server_time?: number;
  used: { cloud_tokens: number; seconds: number }; records: BenchRecord[]; aggregate: BenchAggregate; commit?: string | null; models?: string[];
}
export interface BenchSummary {
  id: string; suite: string; state: string; started: number; commit?: string | null; models: string[]; runs: number; passed: number; rate: number | null;
  false_pass: number; stable_level: number | null; ceiling_level: number | null; weighted_score: number | null; cloud_tokens: number; seconds: number;
}
