import type { ApplicationState } from "@/lib/api";

// domain/enums.py ApplicationState 순서 그대로 — 진행 stepper 가 이 순서로 그린다.
export const PROGRESS_STEPS: { state: ApplicationState; label: string }[] = [
  { state: "collecting", label: "공고 수집" },
  { state: "evaluating", label: "지원 가능성 평가" },
  { state: "generating_resume", label: "이력서 생성" },
  { state: "reviewing", label: "이력서 검토" },
  { state: "rendering_pdf", label: "PDF 렌더링" },
  { state: "awaiting_approval", label: "승인 대기" },
  { state: "scheduled", label: "예약됨" },
  { state: "executing", label: "제출 실행" },
  { state: "verifying", label: "제출 확인" },
  { state: "completed", label: "완료" },
];

export const STATE_LABELS: Record<ApplicationState, string> = {
  collecting: "공고 수집",
  evaluating: "지원 가능성 평가",
  generating_resume: "이력서 생성",
  reviewing: "이력서 검토",
  rendering_pdf: "PDF 렌더링",
  awaiting_approval: "승인 대기",
  scheduled: "예약됨",
  executing: "제출 실행",
  repairing: "레시피 수선 중",
  verifying: "제출 확인",
  completed: "완료",
  rejected: "거절됨",
  cancelled: "취소됨",
  expired: "승인 시간 초과",
  needs_human: "사람 확인 필요",
};

export type StateTone = "default" | "success" | "destructive" | "warning";

export function stateTone(state: ApplicationState): StateTone {
  switch (state) {
    case "completed":
      return "success";
    case "rejected":
    case "cancelled":
    case "expired":
    case "needs_human":
      return "destructive";
    case "awaiting_approval":
    case "repairing":
      return "warning";
    default:
      return "default";
  }
}
