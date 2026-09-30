// 온보딩 초안 — ai/profile_extraction.py · services/profile_draft_types.py 를 옮긴 타입 (§A7 온보딩 추출).
import { request, uploadFile } from "@/lib/api";
import type { Experience, ExperienceKind } from "@/lib/knowledge-api";
import type { Profile, ProfileBody } from "@/lib/profile-api";

export interface ExtractedFact {
  text: string;
  skills: string[];
}

export interface ExtractedSection {
  title: string;
  period: string;
  facts: ExtractedFact[];
}

// id·section key 는 확정할 때 서버가 발급한다 — 초안에는 없다.
export interface ExtractedExperience {
  kind: ExperienceKind;
  name: string;
  role: string;
  period: string;
  url: string | null;
  skills: string[];
  facts: ExtractedFact[];
  sections: ExtractedSection[];
}

// 서버 `ExtractedProfile` 은 Profile 에서 user_id 만 뺀 모양이라 ProfileBody 와 같다.
export interface ProfileExtraction {
  profile: ProfileBody;
  experiences: ExtractedExperience[];
}

// 서버 enum ProfileField — 확정할 때 고르는 인적사항 단위.
export const PROFILE_FIELDS = [
  "name",
  "phone",
  "email",
  "links",
  "education",
  "skills",
  "languages",
  "additional",
] as const;
export type ProfileField = (typeof PROFILE_FIELDS)[number];

// 백엔드 이름은 ProfileDraft — 화면 양식 모델(profile-form 의 ProfileDraft)과 겹쳐 이름을 달리한다.
export interface ExtractionDraft {
  id: string;
  user_id: string;
  source: "resume" | "v2_yaml";
  document_id: string | null;
  source_filename: string | null;
  redacted_identifiers: number; // LLM 전에 가린 주민번호 꼴 개수 — 값은 어디에도 없다 (절대 규칙 5)
  created_at: string;
  content: ProfileExtraction;
}

export interface DraftSummary {
  id: string;
  source: ExtractionDraft["source"];
  source_filename: string | null;
  document_id: string | null;
  created_at: string;
  profile_field_count: number;
  experience_count: number;
  redacted_identifiers: number;
}

export interface DraftSelection {
  profile_fields: ProfileField[];
  experience_indexes: number[]; // content.experiences 의 위치
}

export interface DraftConfirmation {
  profile: Profile | null; // 인적사항 필드를 하나도 안 골랐으면 null (건드리지 않음)
  experiences: Experience[];
}

// 서버가 받는 이력서 형식(PDF·DOCX)만 — 이미지는 글자를 못 뽑는다.
export const RESUME_ACCEPT = ".pdf,.docx";

const path = (id: string) => `/api/profile/drafts/${encodeURIComponent(id)}`;

export const draftApi = {
  list: () => request<DraftSummary[]>("/api/profile/drafts"),
  get: (id: string) => request<ExtractionDraft>(path(id)),
  // 문서함에 저장하지 않고 바로 추출한다. LLM 호출이라 수십 초 걸릴 수 있다.
  upload: (file: File) => uploadFile<ExtractionDraft>("/api/profile/drafts/upload", file),
  save: (id: string, content: ProfileExtraction) =>
    request<ExtractionDraft>(path(id), { method: "PUT", body: JSON.stringify(content) }),
  remove: (id: string) => request<void>(path(id), { method: "DELETE" }),
  confirm: (id: string, selection: DraftSelection) =>
    request<DraftConfirmation>(`${path(id)}/confirm`, { method: "POST", body: JSON.stringify(selection) }),
};
