import { Navigate } from "@tanstack/react-router";
import { QueryStatus } from "@/components/QueryStatus";
import { useProfile } from "@/lib/queries";

// `/` — 인적사항을 아직 저장한 적 없으면(404) 이력서로 시작하는 온보딩으로, 있으면 인적사항으로.
export function StartRedirect() {
  const profile = useProfile();
  if (!profile.isSuccess) return <QueryStatus query={profile} />;
  return <Navigate to={profile.data === null ? "/onboarding" : "/profile"} replace />;
}
