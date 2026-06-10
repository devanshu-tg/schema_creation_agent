// TypeScript types mirroring the FastAPI Pydantic models.

export type UseCase =
  | 'FRAUD'
  | 'ENTITY_RESOLUTION'
  | 'CUSTOMER_360'
  | 'RECOMMENDATION';

export type DataKind =
  | 'INT'
  | 'FLOAT'
  | 'STRING'
  | 'DATETIME'
  | 'BOOL'
  | 'CATEGORICAL'
  | 'ID_LIKE';

export type EdgeDirection = 'DIRECTED' | 'UNDIRECTED' | 'DIRECTED_WITH_REVERSE';

export interface UseCaseInfo {
  id: UseCase;
  name: string;
  version: string;
  description: string;
  vertex_count: number;
  edge_count: number;
  target_question_count: number;
}

export interface ColumnProfile {
  name: string;
  dtype: DataKind;
  null_pct: number;
  distinct_count: number;
  row_count: number;
  cardinality: string;
  is_primary_key_candidate: boolean;
  is_foreign_key_candidate: boolean;
  name_pattern_hits: string[];
  pii_class: string;
  sample_values: string[];
}

export interface TableProfile {
  name: string;
  row_count: number;
  columns: ColumnProfile[];
  primary_key: string[] | null;
  has_event_signature: boolean;
  has_join_signature: boolean;
  is_wide_denormalized: boolean;
  detected_delimiter: string;
}

export interface Attribute {
  name: string;
  dtype: DataKind;
  source_table: string;
  source_column: string;
  pii_class: string;
  nullable: boolean;
}

export interface VertexSource {
  kind: string;
  table: string;
  columns: string[];
}

export interface Vertex {
  name: string;
  primary_id: string;
  primary_id_dtype: DataKind;
  attributes: Attribute[];
  source: VertexSource;
  rationale: string;
  pattern_origin: string | null;
}

export interface Edge {
  name: string;
  from_vertex: string;
  to_vertex: string;
  direction: EdgeDirection;
  reverse_edge_name: string | null;
  attributes: Attribute[];
  rationale: string;
  pattern_origin: string | null;
}

export interface TargetQuestion {
  id: string;
  text: string;
  required_vertices: string[];
  required_edges: string[];
  max_hops: number;
}

export interface Assumption {
  text: string;
  evidence: string;
  confidence: 'high' | 'medium' | 'low';
}

export interface BusinessContext {
  domain: string;
  sub_scenarios: string[];
  goal_type: '' | 'detection' | 'investigation' | 'explainability' | 'risk_scoring';
  business_questions: string[];
  stakeholders: string[];
}

export interface DesignRationale {
  bullets: string[];
}

export interface RecommendedEntity {
  name: string;
  one_liner: string;
}

export interface RecommendationSummary {
  entities: RecommendedEntity[];
  expected_outcomes: string[];
  future_enhancements: string[];
}

export interface Schema {
  use_case: UseCase;
  name: string;
  version: string;
  pattern_version: string | null;
  vertices: Vertex[];
  edges: Edge[];
  target_questions: TargetQuestion[];
  generated_at: string;
  inputs_hash: string;
  assumptions?: Assumption[];
  business_context?: BusinessContext | null;
  design_rationale?: DesignRationale | null;
  recommendation?: RecommendationSummary | null;
}

export type Confidence = 'High' | 'Medium' | 'Low';

export interface ValidationResult {
  passed: boolean;
  checks: { id: string; name: string; passed: boolean; detail: string }[];
  answerable_questions: string[];
  unanswerable_questions: string[];
  structural_warnings: string[];
}

export interface SchemaScore {
  total: number;
  breakdown: Record<string, number>;
  strengths: string[];
  gaps: string[];
  suggestions: string[];
}

export interface CriticReview {
  grade: string;
  overall_judgment: string;
  strengths: string[];
  improvements: string[];
  motive_match: string;
  next_step_suggestion: string;
}

export interface RunResponse {
  workspace_id: string;
  profiles: TableProfile[];
  schema: Schema;
  validation: ValidationResult;
  score: SchemaScore;
  gsql: string;
  markdown: string;
  design_mode: 'ai' | 'deterministic';
  design_info: Record<string, unknown>;
  critic: CriticReview | null;
}

// ---------- Conversational chat agent ----------

export type ChatMessageType =
  | 'question'
  | 'answer'
  | 'propose_schema'
  | 'update_schema'
  | 'kickoff'
  | 'progress';

export interface ChatMessage {
  role: 'user' | 'agent' | 'system';
  content: string;
  type: ChatMessageType;
  schema_json?: Schema | null;
  suggested_replies?: string[];
  timestamp?: string;
}

export interface ChatTurnResponse {
  workspace_id: string;
  messages: ChatMessage[];
  latest: ChatMessage;
  schema: Schema | null;
  score: SchemaScore | null;
  validation: ValidationResult | null;
}

// ---------- Agentic SSE events ----------

export interface PlanEvent {
  steps: string[];
}

export interface ThinkingEvent {
  text: string;
}

export interface ToolCallEvent {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ToolResultEvent {
  id: string;
  name: string;
  ok: boolean;
  summary: string;
}

export interface SchemaUpdateEvent {
  schema: Schema;
}

export interface AgentFinalEvent {
  type: 'propose_schema' | 'update_schema' | 'answer' | 'question';
  message: string;
  suggested_replies: string[];
  schema: Schema | null;
  validation: ValidationResult | null;
  score: SchemaScore | null;
  confidence?: Confidence;
}

export interface AgentErrorEvent {
  message: string;
  code?: string;
}

// ---------- Deploy ----------

export interface DeployPreview {
  workspace_id: string;
  graph_name: string;
  dry_run_plan: string;
}

export type DeployPhase =
  | 'spawn'
  | 'validate'
  | 'drop'
  | 'drop_query'
  | 'vertex'
  | 'edge'
  | 'graph'
  | 'verify'
  | 'loading_job'
  | 'run_load'
  | 'counts'
  | 'log';

export type DeployStepStatus = 'running' | 'ok' | 'failed' | 'info';

export interface DeployStepEvent {
  phase: DeployPhase;
  name: string;
  status: DeployStepStatus;
  summary: string;
}

export interface DeployCountEvent {
  vertex: string;
  count: number;
}

export interface DeployDoneEvent {
  graph_name: string;
  vertex_counts: Record<string, number | null>;
  errors: unknown[];
}

// ---------- Starter queries ----------

export interface StarterQueryItem {
  name: string;
  description: string;
  business_question: string;
  gsql: string;
  expected_output_description: string;
  validated: boolean;
  validation_error: string | null;
}

export interface StarterQueriesResponse {
  workspace_id: string;
  graph_name: string;
  queries: StarterQueryItem[];
}

export interface InstallStarterQueryResponse {
  workspace_id: string;
  query_name: string;
  ok: boolean;
  summary: string;
  error: string | null;
}
