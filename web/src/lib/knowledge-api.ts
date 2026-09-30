// contracts/experience.py · contracts/knowledge.py · api/schemas.py 를 옮긴 타입. 백엔드가 진짜 계약이다.
import { request, uploadFile } from "@/lib/api";

export const EXPERIENCE_KINDS = ["company", "project", "activity", "education"] as const;
export type ExperienceKind = (typeof EXPERIENCE_KINDS)[number];

export interface ExperienceFact {
  id: string;
  text: string;
  skills: string[];
}

export interface ExperienceSection {
  key: string;
  title: string;
  period: string;
  facts: ExperienceFact[];
}

export interface Experience {
  id: string;
  user_id: string;
  kind: ExperienceKind;
  name: string;
  role: string;
  period: string;
  url: string | null;
  skills: string[];
  document_ids: string[];
  facts: ExperienceFact[];
  sections: ExperienceSection[];
}

// 요청 본문: fact id 를 비우면(null) 서버가 발급한다. 기존 fact 는 id 를 그대로 보내야 이력서 근거가 유지된다.
export interface ExperienceFactBody {
  id: string | null;
  text: string;
  skills: string[];
}

export interface ExperienceSectionBody {
  key: string;
  title: string;
  period: string;
  facts: ExperienceFactBody[];
}

export interface ExperienceBody extends Omit<Experience, "id" | "user_id" | "facts" | "sections"> {
  facts: ExperienceFactBody[];
  sections: ExperienceSectionBody[];
}

export interface Answer {
  id: string;
  user_id: string;
  question_key: string; // 서버가 정규화한 질문 — 원문 필드가 따로 없어 화면 표시도 이것이다 (§A7)
  answer: string;
  source_application_id: string | null;
  updated_at: string;
}

export interface AnswerBody {
  question: string;
  answer: string;
  source_application_id: string | null;
}

export interface DocumentMeta {
  id: string;
  user_id: string;
  kind: "uploaded" | "generated";
  filename: string;
  content_type: string;
  size_bytes: number;
  blob_key: string;
  created_at: string;
  fact_ids: string[];
}

// 서버 허용 목록(domain/uploads.py)과 같게 — 파일 선택 창을 거르는 용도일 뿐, 판정은 서버가 한다.
export const DOCUMENT_ACCEPT = ".pdf,.docx,.png,.jpg,.jpeg";

const json = (body: unknown) => JSON.stringify(body);

export const experienceApi = {
  list: () => request<Experience[]>("/api/experiences"),
  create: (body: ExperienceBody) =>
    request<Experience>("/api/experiences", { method: "POST", body: json(body) }),
  update: (id: string, body: ExperienceBody) =>
    request<Experience>(`/api/experiences/${encodeURIComponent(id)}`, { method: "PUT", body: json(body) }),
  remove: (id: string) =>
    request<void>(`/api/experiences/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

export const answerApi = {
  list: () => request<Answer[]>("/api/answers"),
  create: (body: AnswerBody) => request<Answer>("/api/answers", { method: "POST", body: json(body) }),
  update: (id: string, body: AnswerBody) =>
    request<Answer>(`/api/answers/${encodeURIComponent(id)}`, { method: "PUT", body: json(body) }),
  remove: (id: string) => request<void>(`/api/answers/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

export const documentApi = {
  list: () => request<DocumentMeta[]>("/api/documents"),
  upload: (file: File) => uploadFile<DocumentMeta>("/api/documents", file),
  remove: (id: string) => request<void>(`/api/documents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  // 새 탭으로 여는 GET 이라 토큰이 필요 없다 (§A10 은 변경 요청만 토큰을 본다).
  contentPath: (id: string) => `/api/documents/${encodeURIComponent(id)}/content`,
};
