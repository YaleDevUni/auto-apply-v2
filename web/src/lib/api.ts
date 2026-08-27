// api/schemas.py 와 domain/enums.py 를 그대로 옮긴 타입. 백엔드가 진짜 계약이다 —
// 필드를 늘릴 땐 여기와 그쪽을 같이 고친다 (docs/architecture/12-web-console.md).

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type ApplicationState =
  | "collecting"
  | "evaluating"
  | "generating_resume"
  | "reviewing"
  | "rendering_pdf"
  | "awaiting_approval"
  | "scheduled"
  | "executing"
  | "repairing"
  | "verifying"
  | "completed"
  | "rejected"
  | "cancelled"
  | "expired"
  | "needs_human";

export const TERMINAL_STATES: ReadonlySet<ApplicationState> = new Set([
  "completed",
  "rejected",
  "cancelled",
  "expired",
  "needs_human",
]);

export type ExecutionMode = "dry_run" | "supervised" | "live";
export type RevisionScope = "specific" | "general";

export interface PersistState {
  application_id: string;
  workflow_run_id: string;
  state: ApplicationState;
  reason: string;
  scheduled_at: string | null;
  submitted_at: string | null;
}

export interface ApplicationView {
  application_id: string;
  state: ApplicationState;
  scheduled_at: string | null;
  attempts: number;
  history: PersistState[];
}

export interface ApplicationListItem {
  application_id: string;
  state: ApplicationState;
  reason: string;
  scheduled_at: string | null;
  submitted_at: string | null;
  company: string | null;
  title: string | null;
  job_url: string | null;
}

export type ApplyOutcome = "started" | "duplicate" | "unsupported_platform" | "not_found";

export interface ApplyByUrlResponse {
  outcome: ApplyOutcome;
  application_id: string | null;
  label: string | null;
  detail: string | null;
}

export interface PendingDecisionResponse {
  has_pending: boolean;
  title: string | null;
  job_url: string | null;
  mode: ExecutionMode | null;
  caution_documents: string[];
  portfolio_filename: string;
  caution_notes: string[];
  resume_url: string | null;
}

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, body || res.statusText);
  }
  if (res.status === 202 && res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const api = {
  listApplications: () =>
    request<{ items: ApplicationListItem[] }>("/applications").then((r) => r.items),

  getApplication: (applicationId: string) =>
    request<ApplicationView>(`/applications/${applicationId}`),

  getPendingDecision: (applicationId: string) =>
    request<PendingDecisionResponse>(`/applications/${applicationId}/pending`),

  applyByUrl: (url: string) =>
    request<ApplyByUrlResponse>("/applications/apply-by-url", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  approve: (applicationId: string) =>
    request<void>(`/applications/${applicationId}/approve`, { method: "POST", body: "{}" }),

  reject: (applicationId: string, reason: string) =>
    request<void>(`/applications/${applicationId}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),

  revise: (applicationId: string, feedback: string, scope: RevisionScope) =>
    request<void>(`/applications/${applicationId}/revise`, {
      method: "POST",
      body: JSON.stringify({ feedback, scope }),
    }),

  cancel: (applicationId: string) =>
    request<void>(`/applications/${applicationId}/cancel`, { method: "POST" }),
};
