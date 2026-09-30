import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { draftApi, type DraftSelection, type ProfileExtraction } from "@/lib/draft-api";
import {
  answerApi,
  documentApi,
  experienceApi,
  type AnswerBody,
  type ExperienceBody,
} from "@/lib/knowledge-api";
import { profileApi, type ProfileBody } from "@/lib/profile-api";

const KEYS = {
  profile: ["profile"],
  experiences: ["experiences"],
  answers: ["answers"],
  documents: ["documents"],
  drafts: ["drafts"],
  draft: (id: string) => ["drafts", id],
} as const;

// 편집 중인 양식을 다른 탭 포커스로 되돌려 쓰지 않는다.
const NO_FOCUS_REFETCH = { refetchOnWindowFocus: false } as const;

export function useProfile() {
  return useQuery({ queryKey: KEYS.profile, queryFn: profileApi.get, ...NO_FOCUS_REFETCH });
}

export function useSaveProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProfileBody) => profileApi.save(body),
    onSuccess: (saved) => qc.setQueryData(KEYS.profile, saved),
  });
}

// ── 경험 ──────────────────────────────────────────────────────────────────
export function useExperiences() {
  return useQuery({ queryKey: KEYS.experiences, queryFn: experienceApi.list, ...NO_FOCUS_REFETCH });
}

export function useSaveExperience() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: ExperienceBody }) =>
      id === null ? experienceApi.create(body) : experienceApi.update(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.experiences }),
  });
}

export function useDeleteExperience() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: experienceApi.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.experiences }),
  });
}

// ── 답변KB ────────────────────────────────────────────────────────────────
export function useAnswers() {
  return useQuery({ queryKey: KEYS.answers, queryFn: answerApi.list, ...NO_FOCUS_REFETCH });
}

export function useSaveAnswer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: AnswerBody }) =>
      id === null ? answerApi.create(body) : answerApi.update(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.answers }),
  });
}

export function useDeleteAnswer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: answerApi.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.answers }),
  });
}

// ── 문서 ──────────────────────────────────────────────────────────────────
export function useDocuments() {
  return useQuery({ queryKey: KEYS.documents, queryFn: documentApi.list });
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: documentApi.upload,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.documents }),
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: documentApi.remove,
    // 서버가 경험의 첨부 참조도 같이 지운다 — 경험 목록도 다시 받는다.
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: KEYS.documents }),
        qc.invalidateQueries({ queryKey: KEYS.experiences }),
      ]),
  });
}

// ── 온보딩 초안 ────────────────────────────────────────────────────────────
export function useDrafts() {
  return useQuery({ queryKey: KEYS.drafts, queryFn: draftApi.list });
}

export function useDraft(id: string) {
  return useQuery({ queryKey: KEYS.draft(id), queryFn: () => draftApi.get(id), ...NO_FOCUS_REFETCH });
}

export function useUploadResume() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: draftApi.upload,
    onSuccess: (draft) => {
      qc.setQueryData(KEYS.draft(draft.id), draft);
      return qc.invalidateQueries({ queryKey: KEYS.drafts, exact: true });
    },
  });
}

export function useSaveDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, content }: { id: string; content: ProfileExtraction }) => draftApi.save(id, content),
    onSuccess: (draft) => qc.setQueryData(KEYS.draft(draft.id), draft),
  });
}

export function useDeleteDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: draftApi.remove,
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: KEYS.draft(id) });
      return qc.invalidateQueries({ queryKey: KEYS.drafts, exact: true });
    },
  });
}

export function useConfirmDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, selection }: { id: string; selection: DraftSelection }) =>
      draftApi.confirm(id, selection),
    // 확정하면 서버가 초안을 지우고 프로필·경험을 바꾼다.
    onSuccess: (_, { id }) => {
      qc.removeQueries({ queryKey: KEYS.draft(id) });
      return Promise.all([
        qc.invalidateQueries({ queryKey: KEYS.drafts, exact: true }),
        qc.invalidateQueries({ queryKey: KEYS.profile }),
        qc.invalidateQueries({ queryKey: KEYS.experiences }),
      ]);
    },
  });
}
