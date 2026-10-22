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
  /** 逐个文件生成时，已完成部分文件的快照；生成中就能在文件树里看到。 Snapshot of the files finished so far while generating one by one. */
  partial_files_ref?: string;
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
  live?: { step: string; model: string | null; chars: number; seconds: number; tail?: string } | null;
  /** 用户点了暂停：在下一个节点边界停住。 The user paused: holds at the next node boundary. */
  paused?: boolean;
  error?: string;
  result?: any;
  run_status?: string;
  generation_progress?: { completed: number; total: number; current?: string } | null;
  last_model_metrics?: { generation_tokens_per_second?: number; [k: string]: unknown } | null;
}

export interface LanguageSummary {
  id: string;
  title?: string;
  ready: boolean;
  default?: boolean;
  ui?: { highlight?: string; verify_label?: string; [k: string]: unknown };
}

export interface LanguageConfigField {
  key: string;
  label: string;
  kind: 'path' | 'text' | 'bool' | 'choice';
  default?: unknown;
  help?: string;
  choices?: string[];
  /** 选项的显示名，与 choices 一一对应。 Display names of the choices, one each. */
  labels?: string[];
}

/** Python 等语言的“项目环境”（.tools/pyenv）。 A language's project environment (.tools/pyenv). */
export interface ProjectEnv {
  exists: boolean;
  path: string;
  python: string;
  version: string;
  size_mb: number;
  max_mb: number;
  over_limit: boolean;
  packages: { name: string; version: string }[];
  policy: 'ask' | 'allow' | 'deny';
  uses_project_env: boolean;
  /** 项目环境里缺的测试工具（选了 pytest 才可能缺）。 Test tools missing from the project environment. */
  tooling_missing?: string[];
  interpreter: string;
  configured_interpreter: string;
  candidates: { path: string; label: string }[];
}

export interface PackageInstall {
  needs_approval: boolean;
  packages?: string[];
  installed?: string[];
  size_mb: number;
  max_mb?: number;
  env?: ProjectEnv;
}

export interface LanguageInfo {
  id: string;
  title: string;
  version?: number;
  capabilities?: string[];
  operations?: Record<string, { id: string; label: string }>;
  readiness: { ok: boolean; details: string[] };
  config?: LanguageConfigField[];
  config_values?: Record<string, unknown>;
  has_environment?: boolean;
  default?: boolean;
  digest?: string;
  error?: string;
}

export interface Languages {
  default: string;
  languages: LanguageInfo[];
  extra_dirs: string[];
}

export interface Bootstrap {
  console_version: string;
  /** 只读演示部署：不能发起或修改任务。 A read-only demo deployment: nothing can be started or changed. */
  demo?: boolean;
  local_models?: { allowed: boolean; reason: string; vram_gb: number | null; ram_gb: number | null };
  runner_ready: boolean;
  /** 运行器文件是否存在（语言的工具链是否就绪看 languages[].ready）。 Whether the runner binary exists (a language's toolchain readiness is languages[].ready). */
  runner_exists?: boolean;
  /** 默认语言 id。 The default language id. */
  language?: string;
  languages?: LanguageSummary[];
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
  /** 服务端报告的提示缓存命中/未命中 token；没有报告就是 null（未知不是 0）。 Server-reported prompt-cache hit/miss tokens; null when not reported. */
  cache_hit_tokens?: number | null;
  cache_miss_tokens?: number | null;
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
    cache_hit_tokens?: number;
    cache_miss_tokens?: number;
    /** 命中 / (命中 + 未命中)；没有任何缓存数据时为 null。 hit / (hit + miss); null when nothing was reported. */
    cache_hit_rate?: number | null;
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
  test_audit?: boolean;
  context_budget?: boolean;
  assertion_referee?: boolean;
  memory_strategy?: 'auto' | 'window' | 'digest_wide' | 'digest' | 'digest_tight';
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
  /** 需求点名了第三方包（只有带“项目环境”的语言才有）。 The goal names third-party packages (only for a language with a project environment). */
  packages?: TriagePackages;
}

export interface TriagePackages {
  named: string[];
  missing: string[];
  policy: 'ask' | 'allow' | 'deny';
  env_exists: boolean;
  size_mb: number;
  max_mb: number;
}

// ───────── 开跑前估计 Pre-run estimate ─────────
export interface EstimateTier {
  key: 'economy' | 'standard' | 'rich';
  label: string;
  tokens: number;
  price: { low: number | null; high: number | null; models: { model: string; per_million: number; cost: number }[]; unpriced: number };
}

export interface Estimate {
  source: 'local-model' | 'rules';
  mode: 'model' | 'rules';
  level: number;
  files: number | null;
  can_handle: 'yes' | 'risky' | 'no';
  reasons: string[];
  tiers: EstimateTier[];
  model: string;
  note: string;
}

// ───────── 工作台：实时文件流、用户输入、调用检视 Workbench: live files, user input, call inspection ─────────
export interface LiveFile {
  path: string;
  content: string;
  /** 这个字符串已经写完（JSON 里闭合了）。 The string is closed in the JSON. */
  complete: boolean;
  /** write = 整份文件；edit = 只是新片段（V2 工人的 edit_file）。 write = the whole file; edit = just the new fragment. */
  kind: 'write' | 'edit';
}

export interface LiveCall {
  step: string;
  invocation?: string | null;
  model?: string | null;
  chars: number;
  seconds: number;
  seq: number;
  truncated: boolean;
  files: LiveFile[];
  tail: string;
}

export interface LiveView {
  live: LiveCall | null;
}

export type InputBody = { kind: 'note'; text: string } | { kind: 'control'; action: 'pause' | 'resume' | 'stop' };

export interface CallRow {
  run_id: string;
  step: string;
  invocation: string;
  attempt: number | null;
  status: 'completed' | 'failed' | 'pending' | 'abandoned';
  model: string;
  kind: 'local' | 'cloud' | 'unknown';
  started: number | null;
  duration_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  cache_hit_tokens: number | null;
  cache_miss_tokens: number | null;
  notes: number;
  error: string | null;
  /** 该模型的上下文窗口（token）与这次提示占它的比例；未知为 null。 The model's context window and the share this prompt took; null when unknown. */
  window: number | null;
  window_percent: number | null;
}

export interface CallSection {
  name: string;
  kind: string;
  chars: number;
  est_tokens: number;
  share: number;
  preview: string;
  entries: { name: string; chars: number; est_tokens: number }[];
}

/** 上下文的类别（与 Claude Code 的“上下文”面板同一种读法）。 A context category (read like Claude Code's context panel). */
export interface CallCategory {
  id: 'system' | 'task' | 'tests' | 'code' | 'memory' | 'evidence' | 'conventions' | 'human' | 'control' | 'other';
  label: string;
  english: string;
  tokens: number;
  share: number;
  sections: string[];
}

export interface CallWindow {
  limit: number | null;
  used: number;
  source: 'server' | 'estimate';
  output_reserve: number | null;
  percent: number | null;
  free: number | null;
}

/** 包工人的“最近结果”窗口：保留几条、更早的几条已离开窗口、笔记与摘要多长。 A package worker's recent-results window. */
export interface WorkingMemory {
  step: number | null;
  max_steps: number | null;
  kept: number;
  dropped: number | null;
  notes_chars: number;
  summary_chars: number;
  digest_lines: number;
  pins: string[];
  strategy: string;
  recall: boolean;
  at_risk: boolean;
  kept_items: { tool: string | null; ok: boolean | null; path: string | null; chars: number }[];
}

export interface CallDetail {
  run_id: string;
  step: string;
  invocation: string;
  attempt: number;
  status: string;
  model: string | null;
  provider: string | null;
  duration_ms: number | null;
  layout: 'stable_first' | 'sorted';
  instruction: { chars: number; est_tokens: number };
  sections: CallSection[];
  categories: CallCategory[];
  window: CallWindow;
  working_memory: WorkingMemory | null;
  delta_vs_previous: { against: string; by_category: Record<string, number>; total: number } | null;
  est_total_tokens: number;
  usage: { prompt_tokens: number | null; completion_tokens: number | null; cache_hit_tokens: number | null; cache_miss_tokens: number | null };
  omitted: { section: string; id: string; reason: string }[];
  context_plans: { where: string; window: number; total: number; kept_tokens: number; outlined: string[]; dropped: string[]; over_budget: boolean }[];
  user_notes: string[];
  prefix_with_previous: { against: { step: string; invocation: string }; common_chars: number; of_chars: number; share: number | null } | null;
  response: { chars: number; keys: string[] | null; preview: string } | null;
  error: string | null;
}

// ───────── 交付报告（/api/projects/:id/delivery）Delivery report ─────────
export interface DeliveryAcceptance {
  index: number;
  text: string;
  status: 'verified' | 'unverified' | 'failed';
  evidence: { operation: string; step_id: string; exit_code: number | null; status: string }[];
  cases: number;
  strength: string | null;
}
export interface Delivery {
  task: string;
  outcome: string;
  complete: boolean;
  reason?: string;
  gate_passed?: boolean;
  language?: string;
  files: string[];
  acceptance: DeliveryAcceptance[];
  assumptions: { source: string; text: string }[];
  independent_evidence: { kind: string; data: unknown }[];
  open_items: { kind: string; text: string }[];
  how_to_run: string;
  smoke: { executed: boolean; reason?: string; exit_code?: number | null; stdout?: string };
  cost: { calls: number; cloud_tokens: number; local_tokens: number; cache_hit_rate: number | null; wall_seconds: number | null; failed_calls: number; human_interventions: number };
}
export interface DeliveryReport {
  delivery: Delivery;
  markdown: string;
  inconsistencies: string[];
}
