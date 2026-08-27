import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, TERMINAL_STATES, type RevisionScope } from "@/lib/api";

const LIST_POLL_MS = 5_000;
const DETAIL_POLL_MS = 3_000;

export function useApplicationList() {
  return useQuery({
    queryKey: ["applications"],
    queryFn: api.listApplications,
    refetchInterval: LIST_POLL_MS,
  });
}

export function useApplication(applicationId: string | null) {
  return useQuery({
    queryKey: ["application", applicationId],
    queryFn: () => api.getApplication(applicationId as string),
    enabled: applicationId !== null,
    // 종결 상태(TERMINAL_STATES)에 도달하면 더 이상 안 바뀌니 폴링을 멈춘다.
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      if (state && TERMINAL_STATES.has(state)) return false;
      return DETAIL_POLL_MS;
    },
  });
}

export function usePendingDecision(applicationId: string | null) {
  return useQuery({
    queryKey: ["pending", applicationId],
    queryFn: () => api.getPendingDecision(applicationId as string),
    enabled: applicationId !== null,
    refetchInterval: (query) => (query.state.data?.has_pending ? DETAIL_POLL_MS : false),
  });
}

function useInvalidateApplication(applicationId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["applications"] });
    void qc.invalidateQueries({ queryKey: ["application", applicationId] });
    void qc.invalidateQueries({ queryKey: ["pending", applicationId] });
  };
}

export function useApplyByUrl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (url: string) => api.applyByUrl(url),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["applications"] }),
  });
}

export function useApprove(applicationId: string) {
  const invalidate = useInvalidateApplication(applicationId);
  return useMutation({
    mutationFn: () => api.approve(applicationId),
    onSuccess: invalidate,
  });
}

export function useReject(applicationId: string) {
  const invalidate = useInvalidateApplication(applicationId);
  return useMutation({
    mutationFn: (reason: string) => api.reject(applicationId, reason),
    onSuccess: invalidate,
  });
}

export function useRevise(applicationId: string) {
  const invalidate = useInvalidateApplication(applicationId);
  return useMutation({
    mutationFn: ({ feedback, scope }: { feedback: string; scope: RevisionScope }) =>
      api.revise(applicationId, feedback, scope),
    onSuccess: invalidate,
  });
}

export function useCancel(applicationId: string) {
  const invalidate = useInvalidateApplication(applicationId);
  return useMutation({
    mutationFn: () => api.cancel(applicationId),
    onSuccess: invalidate,
  });
}
