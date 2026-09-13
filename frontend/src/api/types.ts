export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type ApprovalStatus = "approved" | "pending" | "denied";
export type ApprovalDecision = "approve" | "reject";
export type AgentRoute = "policy" | "operations" | "web";

export interface HealthResponse {
  request_id: string;
  status: "alive";
  service: string;
}

export interface DependencyStatus {
  name: string;
  status: "ready" | "unavailable" | "disabled";
  required: boolean;
  detail: string;
}

export interface ReadinessResponse {
  request_id: string;
  status: "ready" | "not_ready";
  dependencies: DependencyStatus[];
}

export interface ServiceInfoResponse {
  request_id: string;
  service: string;
  version: string;
  environment: string;
  deployment_profile: "local" | "huggingface" | "azure";
  backend: string;
  model_id: string;
  inference_provider: string;
  inference_configured: boolean;
  inference_state:
    | "not_initialized"
    | "loaded"
    | "unconfigured"
    | "not_used"
    | "requesting"
    | "available"
    | "unavailable";
  demo_state: "durable" | "ephemeral";
  build_sha: string | null;
}

export interface AgentQueryResponse {
  request_id: string;
  status: "completed";
  route: AgentRoute;
  routing_decision: Record<string, JsonValue>;
  result: JsonValue;
}

export interface PolicyEvidence {
  citation_id: string;
  policy_id: string;
  title: string;
  domain: string;
  chunk_index: number;
  content: string;
}

export interface PolicyQueryResponse {
  request_id: string;
  status: "answered" | "insufficient_evidence";
  source_scope: "internal_policy";
  answer: string;
  citations: string[];
  citation_valid: boolean;
  evidence: PolicyEvidence[];
}

export interface AccessRequestRecord {
  request_id: string;
  subject_type: "employee" | "contractor";
  subject_id: string;
  system_name: string;
  requested_role: string;
  approval_status: ApprovalStatus;
  requested_start_date: string;
  requested_end_date: string | null;
}

export interface AccessRequestListResponse {
  request_id: string;
  access_requests: AccessRequestRecord[];
}

export interface AccessRequestDetailResponse {
  request_id: string;
  access_request: AccessRequestRecord;
}

export interface ActionProposal {
  action_type: string;
  summary: string;
  arguments: Record<string, JsonValue>;
  consequence: string;
}

export interface ApprovalProposalResponse {
  request_id: string;
  status: "approval_required";
  approval_required: true;
  approval_id: string;
  proposal: ActionProposal;
}

export interface ApprovalResumeResponse {
  request_id: string;
  approval_id: string;
  status: "approved" | "rejected";
  action_result: JsonValue | null;
}

export interface ApiErrorBody {
  request_id: string;
  status: "error";
  error: {
    code: string;
    message: string;
  };
}
