import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { profileApi, type ProfileBody } from "@/lib/profile-api";

const PROFILE_KEY = ["profile"] as const;

export function useProfile() {
  return useQuery({
    queryKey: PROFILE_KEY,
    queryFn: profileApi.get,
    // 편집 중인 양식을 다른 탭 포커스로 되돌려 쓰지 않는다.
    refetchOnWindowFocus: false,
  });
}

export function useSaveProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProfileBody) => profileApi.save(body),
    onSuccess: (saved) => qc.setQueryData(PROFILE_KEY, saved),
  });
}
