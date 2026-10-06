// 后端读取模型的类型契约（只描述前端用到的字段；其余字段允许透传）。
// Typed contract for the backend read models; unknown fields pass through.

export type RunStatus = 'created' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled' | 'needs_attention' | string;

export interface Clarification {
  reason: string;
  questions: { key: string; title: string; options: { id: string; label: string }[] }[];
}

export interface ProjectPlan {
  kind?: 'code' | 'plan';
  status: 'waiting_for_input' | 'awaiting_review' | 'approved' | 'planning' | 'generating' | 'failed' | string;
  review_mode?: 'automatic' | 'manual';
  spec_ref?: string;
  checks_ref?: string;
  files_ref?: string;
  approval_ref?: string;
  base_approval_ref?: string;
  repair_of?: string;
  revision_scope?: 'tests' | string;
  changed_files?: string[];
  gen_progress?: { completed: number; total: number; current?: string } | null;
  clarification_id?: string;
  clarification?: Clarification;
  coverage_warning?: string;
  triage?: Triage;
  test_review?: { findings: { severity: string; message: string }[] };
  error?: string;
}

export interface Run {
  id: string;
  status: RunStatus;
  reason: string;
  cancel_requested?: number | boolean;
  data: {
    goal: string;
    workspace: string;
    created_at?: number;
    parent_run_id?: string | null;
    project_plan?: ProjectPlan;
    project_bundle?: { approval_ref: string };
    [k: string]: unknown;
  };
}

export interface RoleCall {
  purpose: string;
  invocation_id: string;
  attempt_no?: number;
  status: string;
  output_ref?: string | null;
  [k: string]: unknown;
}

export interface RunEvent {
  seq: number;
  type: string;
  payload: Record<string, any>;
  created: number;
}

export interface Detail {
  run: Run;
  role_calls: RoleCall[];
  role_active: boolean;
  steps: { id: string; status: string; result_ref?: string }[];
  events: RunEvent[];
  event_count: number;
  tools: { id: string; result_ref?: string; status?: string }[];
  attempts: unknown[];
  active: boolean;
  worker_error?: string | null;
  pause_requested?: boolean;
}

export type StageStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'blocked' | 'cancelled' | string;

export interface Stage {
  id: string;
  run_id: string;
  role: string;
  label: string;
  status: StageStatus;
  kind: 'role' | 'human' | 'gate' | string;
  response_ref?: string | null;
  reused?: boolean;
  checks?: { id: string; status: string }[];
}

export interface Version {
  run_id: string;
  status: RunStatus;
  kind: 'plan' | 'code' | 'verification';
  parent_run_id?: string | null;
}

export interface ProjectView {
  id: string;
  title: string;
  selected_run_id: string;
  stages: Stage[];
  versions: Version[];
}

export interface ProjectSummary {
  id: string;
  title: string;
  current_run_id: string;
  status: RunStatus;
  revision_count: number;
}

export interface Job {
  job_id?: string;
  status: 'running' | 'completed' | 'failed' | 'interrupted' | 'waiting_for_input' | 'cancelled' | string;
  run_id?: string | null;
  mode?: string;
  phase?: string;
  stage?: string;
  started?: number;
  created_at?: number;
  model?: string | null;
  /** 此刻真正在用的模型（来自账本事件），不是选择框里的默认模型。 The model in use right now, from ledger events. */
  current_model?: string | null;
  current_kind?: 'local' | 'cloud' | null;
  /** 流式传输的实时进度：此刻这次模型调用已生成的字符数与用时。 Live streaming progress of the in-flight model call. */
  live?: { step: string; model: string | null; chars: number; seconds: number } | null;
  error?: string;
  result?: any;
  run_status?: string;
  generation_progress?: { completed: number; total: number; current?: string } | null;
  last_model_metrics?: { generation_tokens_per_second?: number; [k: string]: unknown } | null;
}

export interface Bootstrap {
  console_version: string;
  /** 只读演示部署：不能发起或修改任务。 A read-only demo deployment: nothing can be started or changed. */
  demo?: boolean;
  runner_ready: boolean;
  active_run: string | null;
  active_job: (Job & { job_id: string }) | null;
  interrupted_jobs: { job_id: string; run_id?: string; phase?: string }[];
  capabilities: Record<string, boolean>;
  routing_defaults?: { policy: RoutingPolicy; budget: RoutingBudget };
}

export interface Profile {
  id: string;
  name?: string;
  base_url: string;
  model: string;
  model_type: 'local' | 'cloud';
  protocol: 'openai' | 'ollama';
  enabled?: boolean;
  ready?: boolean;
  key_configured?: boolean;
  level?: number;
  priority?: number;
  roles?: string[];
  context_limit?: number;
  timeout_seconds?: number;
  max_output_tokens?: number;
  token_parameter?: string;
  thinking?: string;
  input_price_per_million?: number | null;
  output_price_per_million?: number | null;
  last_test?: { ok: boolean; [k: string]: unknown } | null;
  [k: string]: unknown;
}

export interface Profiles {
  profiles: Profile[];
  active_id: string;
  key_storage?: string;
  [k: string]: unknown;
}

export interface CheckResult {
  id: string;
  operation: string;
  result?: {
    status: string;
    exit_code?: number | null;
    duration_ms?: number;
    stdout?: string;
    stderr?: string;
    error?: string;
    truncated?: boolean;
  } | null;
}

export interface RepairAdvice {
  action: 'repair' | 'revise_tests' | 'format_tests' | string;
  message: string;
}

export interface SourceFinding {
  path: string;
  line?: number;
  message: string;
}

// ───────── 任务报告（/api/projects/:id/report）Task report ─────────
export interface ModelCall {
  run_id: string;
  step_id: string | null;
  invocation_id: string | null;
  attempt_no: number | null;
  status: 'completed' | 'failed' | 'pending' | 'abandoned';
  model: string;
  provider?: string | null;
  kind: 'local' | 'cloud' | 'unknown';
  started: number | null;
  finished: number | null;
  duration_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  tokens_per_second?: number | null;
  error?: string | null;
}

export interface ToolCallRow {
  run_id: string;
  step_id: string;
  operation: string;
  kind: 'tool' | 'gate' | 'app';
  status: string;
  started: number | null;
  finished: number | null;
  duration_ms: number | null;
  exit_code: number | null;
  isolated: boolean;
}

export interface UsageRow {
  calls: number;
  failed: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  model_ms: number;
}

export interface TaskReport {
  project_id: string;
  selected_run_id: string;
  latest_run_id: string;
  title: string;
  outcome: 'succeeded' | 'failed' | 'cancelled' | 'waiting' | 'running';
  started_at: number | null;
  ended_at: number | null;
  wall_seconds: number | null;
  totals: {
    calls: number;
    failed_calls: number;
    unknown_usage_calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    local_tokens: number;
    cloud_tokens: number;
    model_ms: number;
  };
  by_model: (UsageRow & { model: string; kind: 'local' | 'cloud' | 'unknown' })[];
  by_step: (UsageRow & { step_id: string | null })[];
  calls: ModelCall[];
  tools: ToolCallRow[];
  versions: { run_id: string; status: string; kind: string }[];
  /** 旧任务没有路由信息，为 null。 Null for legacy tasks. */
  routing: RoutingReport | null;
  /** 修复过程事件：子图节点、诊断、模型释放、轮数延长等。 Repair-process events. */
  process?: ProcessEvent[];
}

export interface ProcessEvent {
  kind: string;
  run_id: string;
  at: number;
  data: Record<string, any>;
}

// ───────── 路由与预算 Routing & budget ─────────
export interface RoutingBudget {
  max_model_calls: number | null;
  max_cloud_tokens: number | null;
  max_active_seconds: number | null;
  max_cost: number | null;
}

export interface RoutingPolicy {
  attempts_per_level: Record<string, number>;
  max_escalations: number;
  planner_retries: number;
  start_level_by_role: Record<string, number>;
  conductor?: boolean;
  conductor_max_calls?: number;
  conductor_cascade?: boolean;
}

export interface RouteDecision {
  run_id: string;
  at: number;
  action: 'use' | 'stop';
  role: string;
  chain: 'planning' | 'generation' | 'fix' | string;
  stage: string;
  reason: string;
  detail?: string;
  escalated: boolean;
  candidate: string | null;
  level: number | null;
  model: string | null;
  model_type: 'local' | 'cloud' | null;
  spend: { calls: number; cloud_tokens: number; active_seconds: number; cost: number };
}

export interface RoutingReport {
  mode: 'ladder' | 'fixed';
  budget: RoutingBudget;
  policy: RoutingPolicy;
  candidates: { id: string; level: number; model: string; model_type: string; digest: string | null; context_limit: number }[];
  spend: { calls: number; cloud_tokens: number; active_seconds: number; cost: number; cost_known: boolean; reserved_calls: number };
  decisions: RouteDecision[];
  escalations: number;
  stopped: { reason: string; detail?: string } | null;
}

// ───────── API 账户 Accounts ─────────
export interface Account {
  id: string;
  base_url: string;
  host: string;
  profiles: { id: string; name: string; model: string; level: number; enabled: boolean }[];
  balance_supported: boolean;
  pricing_source: string | null;
}

export interface AccountModel {
  id: string;
  name: string;
  context_window: number | null;
  max_output_tokens: number | null;
  vision: boolean;
  efforts: string[] | null;
  added: boolean;
  suggestion: { level: number; price_in: number; price_out: number; currency: string; source: string; note: string } | null;
}

export interface AccountBalance {
  supported: boolean;
  reason?: string;
  available?: boolean;
  balances?: { currency: string; total: string; granted: string; topped_up: string }[];
}

// ───────── 可行性预检 Feasibility triage ─────────
export interface TriageFinding {
  rule: string;
  level: 'infeasible' | 'risky';
  matched: string[];
  reason: string;
  suggestion: string;
}

export interface Triage {
  verdict: 'ok' | 'risky' | 'infeasible';
  summary?: string;
  findings: TriageFinding[];
  /** 本地模型的复核意见：只能告警，不拦截。 Local-model review: warns only. */
  model?: { verdict: 'ok' | 'risky' | 'skipped'; reasons: string[]; suggestions: string[]; by?: string };
}
