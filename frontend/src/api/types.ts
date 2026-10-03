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
  error?: string;
  result?: any;
  run_status?: string;
  generation_progress?: { completed: number; total: number; current?: string } | null;
  last_model_metrics?: { generation_tokens_per_second?: number; [k: string]: unknown } | null;
}

export interface Bootstrap {
  console_version: string;
  runner_ready: boolean;
  active_run: string | null;
  active_job: (Job & { job_id: string }) | null;
  interrupted_jobs: { job_id: string; run_id?: string; phase?: string }[];
  capabilities: Record<string, boolean>;
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
