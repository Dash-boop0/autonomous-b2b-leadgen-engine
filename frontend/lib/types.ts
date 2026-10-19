// Types mirror the backend shared interfaces (app/schemas.py) and API responses.

export type Role = "admin" | "operator" | "reviewer" | "viewer";
export type BuyerRole = "economic_buyer" | "champion" | "user" | "technical" | "unknown";
export type EmailStatus =
  | "unknown"
  | "syntax_valid"
  | "syntax_invalid"
  | "domain_has_mx"
  | "provider_verified"
  | "deliverable_confirmed"
  | "bounced";
export type Tier = "A" | "B" | "C" | "disqualified";

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  permissions?: string[];
}

export interface SystemState {
  emergency_stop: boolean;
  stop_reason: string | null;
  stop_set_by: string | null;
  stop_set_at: string | null;
  send_mode: "dry_run";
  sim_clock_offset_days: number;
  app_mode: string;
  seeded_profile: string | null;
  providers: Record<string, { name: string; mock: boolean }>;
}

export interface ChecklistItem {
  key: string;
  label: string;
  done: boolean;
  href: string;
}

export interface RunSummary {
  id: string;
  workflow: string;
  title: string;
  status: string;
  created_at: string;
}

export interface Dashboard {
  counts: Record<string, number>;
  active_icp: { id: string; version: number; name: string } | null;
  latest_campaign: { id: string; name: string; status: string } | null;
  checklist: ChecklistItem[];
  recent_runs: RunSummary[];
}

export interface Criterion {
  name: string;
  weight: number;
  raw_value: unknown;
  score_0_1: number;
  contribution: number;
  evidence_ids: string[];
  missing: boolean;
  note?: string;
}

export interface Qualification {
  id: string;
  entity: string;
  model_version: string;
  hard_disqualifiers: { rule: string; reason: string }[];
  criteria: Criterion[];
  total_score_0_100: number;
  tier: Tier;
  explained_at: string;
  explanation: string;
  missing_data: string[];
}

export interface Account {
  id: string;
  name: string;
  domain: string | null;
  industry: string | null;
  employee_band: string | null;
  revenue_band: string | null;
  country: string | null;
  region: string | null;
  business_model: string | null;
  source_ids: string[];
  first_seen_at: string;
  last_verified_at: string | null;
  signals: string[] | null;
  contacts?: number;
  score?: number | null;
  tier?: Tier | null;
  model_version?: string | null;
  missing_data?: string[];
}

export interface VerificationCheck {
  check: string;
  result: string;
  provider: string;
  mock: boolean;
  simulated?: boolean;
  at: string;
}

export interface Contact {
  id: string;
  account_id: string;
  full_name: string;
  title: string | null;
  seniority: string | null;
  department: string | null;
  buyer_role: BuyerRole;
  email: string | null;
  email_status: EmailStatus;
  phone: string | null;
  linkedin_url?: string | null;
  source_ids: string[];
  consent_basis: string;
  suppressed: boolean;
  suppression_reason: string | null;
  account_name?: string | null;
  verification_checks?: VerificationCheck[];
  role_match_reason?: string | null;
  lead_stage?: string | null;
  lead_id?: string | null;
}

export interface Evidence {
  id: string;
  entity_type: string;
  entity_id: string;
  claim: string;
  excerpt: string;
  source_id: string | null;
  confidence: number;
  created_by: "agent" | "human";
  created_at: string;
  kind?: string;
  agent?: string | null;
}

export interface SourceRecord {
  id: string;
  adapter: string;
  source_type: string;
  url_or_ref: string;
  retrieved_at: string;
  terms_note: string;
  confidence: number;
  raw_hash: string;
}

export interface AccountDetail {
  account: Account;
  qualification: Qualification | null;
  score_history: { id: string; total: number; tier: Tier; model_version: string; at: string }[];
  evidence: Evidence[];
  sources: SourceRecord[];
  contacts: Contact[];
}

export interface Adapter {
  id: string;
  name: string;
  source_type: string;
  kind: string;
  enabled: boolean;
  mock: boolean;
  auth: string;
  allowlist: string[];
  blocklist: string[];
  rate_limit_per_minute: number;
  cache_ttl_seconds: number;
  retention_days: number;
  terms_note: string;
  fields: string[];
  failure_handling: string;
  records: number;
  requests_total: number;
}

export interface CsvUpload {
  id: string;
  filename: string;
  kind: "accounts" | "contacts";
  sha256: string;
  row_count: number;
  uploaded_by: string;
  uploaded_at: string;
  consumed_at: string | null;
}

export interface ICPVersion {
  id: string;
  version: number;
  status: string;
  offer_id: string | null;
  name: string;
  definition: Record<string, unknown> & {
    industries?: string[];
    adjacent_industries?: string[];
    countries?: string[];
    employee_bands?: string[];
    buyer_roles?: string[];
    signals?: string[];
  };
  weights: Record<string, number>;
  hard_disqualifiers: { rule: string; values?: string[]; reason: string }[];
  tier_thresholds: { A: number; B: number };
  rationale: string;
  change_summary: string;
  parent_version_id: string | null;
  created_by: string;
  created_at: string;
  approved_at: string | null;
  model_version: string;
  research?: { id: string; kind: string; name: string; data: Record<string, unknown>; evidence_ids: string[]; agent: string; confidence: number }[];
  evidence?: Evidence[];
  pending_approval_id?: string | null;
  criteria?: string[];
}

export interface WorkflowStep {
  id: string;
  idx: number;
  name: string;
  agent: string | null;
  status: string;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string | null;
  output: Record<string, unknown>;
  error: string | null;
  approval_id: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Run {
  id: string;
  workflow: string;
  title: string;
  status: string;
  input: Record<string, unknown>;
  created_by: string;
  created_at: string;
  updated_at: string;
  error: string | null;
  steps: WorkflowStep[];
}

export interface AdvanceResult {
  run_id: string;
  outcome: string;
  status: string;
  ticks: number;
}

export interface WorkflowDefinition {
  name: string;
  title: string;
  description: string;
  agents: string[];
  steps: { name: string; agent: string | null; title: string; max_attempts: number; kind: string }[];
}

export interface Approval {
  id: string;
  subject_type: string;
  subject_id: string;
  action: string;
  requested_by: { actor_type: "agent" | "human"; actor_id: string };
  status: "pending" | "approved" | "rejected" | "expired";
  decided_by: string | null;
  decided_at: string | null;
  reason: string | null;
  policy_id: string;
  summary: string;
  payload: Record<string, unknown>;
  run_id: string | null;
  initiated_by: string | null;
  created_at: string;
  subject_name?: string | null;
}

export interface Campaign {
  id: string;
  name: string;
  icp_version_id: string;
  status: string;
  segment_filter: { tiers?: string[]; industries?: string[] };
  sequence: { step: number; delay_days: number; template: string }[];
  frequency_cap: { max_per_contact_per_7d?: number; max_sends_per_day?: number };
  schedule: Record<string, string>;
  strategy: { segments?: { name: string; angle: string; lead_ids: string[] }[]; excluded?: Record<string, number> };
  created_by: string;
  created_at: string;
  approval_id: string | null;
  drafts?: number;
  queue?: Record<string, number>;
}

export interface PolicyCheck {
  rule: string;
  passed: boolean;
  detail: string;
}

export interface Draft {
  id: string;
  campaign_id: string;
  lead_id: string;
  contact_id: string;
  step_no: number;
  subject: string;
  body: string;
  evidence_ids: string[];
  personalisation_level: "evidence_grounded" | "generic";
  policy_checks: PolicyCheck[];
  policy_passed: boolean;
  status: string;
  llm_provider: string;
  contact_name: string | null;
  contact_email: string | null;
  account_name: string | null;
  evidence: { id: string; claim: string; excerpt: string; source_id: string | null; confidence: number }[];
}

export interface QueueItem {
  id: string;
  campaign_id: string;
  draft_id: string;
  contact_id: string;
  step_no: number;
  to_email: string;
  scheduled_for: string;
  status: string;
  status_reason: string | null;
  provider_message_id: string | null;
  sent_at: string | null;
  mode: string;
}

export interface CampaignDetail {
  campaign: Campaign;
  drafts: Draft[];
  queue: QueueItem[];
  runs: { id: string; workflow: string; status: string; created_at: string }[];
}

export interface Reply {
  id: string;
  event_id: string;
  contact_id: string;
  campaign_id: string | null;
  body: string;
  classification: string;
  confidence: number;
  classifier: string;
  needs_human: boolean;
  status: string;
  response_status: string;
  suggested_response: string | null;
  response_citations: { id: string; doc: string; section: string; version: string }[];
  escalation_reason: string | null;
  triaged_by: string | null;
  created_at: string;
  contact_name: string | null;
  contact_email: string | null;
  account_name: string | null;
  suppressed: boolean;
}

export interface Ratio {
  numerator: number;
  denominator: number;
  value: number | null;
  formula: string;
}

export interface MetricsBlock {
  counts: Record<string, number>;
  rates: Record<string, Ratio>;
}

export interface Analytics extends MetricsBlock {
  campaign_id: string;
  simulated: boolean;
  segments: Record<string, (MetricsBlock & { segment: string })[]>;
  recommendations: { kind: string; summary: string; change: Record<string, string>; evidence: string; applied_to_draft: boolean }[];
  narrative: string | null;
  recommendation_run: Run | null;
  proposals: ICPVersion[];
}

export interface AgentInfo {
  name: string;
  group: string;
  description: string;
  tools: string[];
  confidence_threshold: number;
  max_retries: number;
  completion_criteria: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  workflows: string[];
}

export interface AuditEvent {
  id: string;
  at: string;
  actor_type: string;
  actor_id: string;
  action: string;
  subject_type: string | null;
  subject_id: string | null;
  before: unknown;
  after: unknown;
  evidence_ids: string[];
  run_id: string | null;
  step_id: string | null;
  severity: string;
}

export interface Suppression {
  id: string;
  email_or_domain: string;
  reason: string;
  created_at: string;
  note: string;
  created_by: string;
}

export interface ErrorItem {
  id: string;
  run_id: string;
  step_id: string;
  workflow: string;
  step_name: string;
  error: string;
  attempts: number;
  status: string;
  created_at: string;
  resolved_by: string | null;
}
