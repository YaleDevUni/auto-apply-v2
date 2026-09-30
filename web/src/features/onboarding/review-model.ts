// 온보딩 초안 검토 상태: 항목마다 채택/버림(체크) + 수정(양식). 확정은 "고친 내용을 초안에 PUT → 고른 항목만 confirm"
// 두 단계다(§A7). 버린 경험도 초안 내용에는 남겨 둔다 — confirm 의 experience_indexes 가 초안 위치를 가리키기 때문이다.
import {
  fromExtracted,
  toExtracted,
  validateExperience,
  type ExperienceErrors,
  type ExperienceForm,
} from "@/features/experiences/experience-form";
import { toBody, toDraft, type ProfileDraft } from "@/features/profile/profile-form";
import {
  PROFILE_FIELDS,
  type DraftSelection,
  type ExtractionDraft,
  type ProfileExtraction,
  type ProfileField,
} from "@/lib/draft-api";
import type { ProfileBody } from "@/lib/profile-api";

export interface ReviewState {
  profile: ProfileDraft;
  experiences: ExperienceForm[];
  fields: Record<ProfileField, boolean>;
  keep: boolean[];
}

export interface ReviewProblems {
  nothingSelected: boolean;
  // 인적사항을 하나라도 고르면 병합 결과에 이름이 있어야 한다 (서버 draft_merge 와 같은 규칙).
  nameMissing: boolean;
  experiences: (ExperienceErrors | null)[];
}

export function hasValue(body: ProfileBody, field: ProfileField): boolean {
  const value = body[field];
  if (typeof value === "string") return value.trim() !== "";
  if (Array.isArray(value)) return value.length > 0;
  return Object.values(value).some((v) => v !== null);
}

// 기본은 "값이 있는 것은 전부 채택" — 사용자는 버릴 것만 끈다.
export function initialReview(draft: ExtractionDraft): ReviewState {
  const { profile, experiences } = draft.content;
  return {
    profile: toDraft(profile),
    experiences: experiences.map(fromExtracted),
    fields: Object.fromEntries(PROFILE_FIELDS.map((f) => [f, hasValue(profile, f)])) as Record<ProfileField, boolean>,
    keep: experiences.map(() => true),
  };
}

export function toContent(state: ReviewState): ProfileExtraction {
  return { profile: toBody(state.profile), experiences: state.experiences.map(toExtracted) };
}

export function toSelection(state: ReviewState): DraftSelection {
  return {
    profile_fields: PROFILE_FIELDS.filter((f) => state.fields[f]),
    experience_indexes: state.keep.flatMap((kept, i) => (kept ? [i] : [])),
  };
}

export function reviewProblems(state: ReviewState, currentName: string | null): ReviewProblems {
  const selection = toSelection(state);
  const anyField = selection.profile_fields.length > 0;
  const draftName = state.fields.name && state.profile.name.trim() !== "";
  return {
    nothingSelected: !anyField && selection.experience_indexes.length === 0,
    nameMissing: anyField && !draftName && !currentName?.trim(),
    // 버린 경험도 초안 PUT 에 실려 서버 검증을 받는다 — 전부 본다.
    experiences: state.experiences.map(validateExperience),
  };
}

export function hasProblems(p: ReviewProblems): boolean {
  return p.nothingSelected || p.nameMissing || p.experiences.some((e) => e !== null);
}
