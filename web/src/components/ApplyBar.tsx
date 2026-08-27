import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useApplyByUrl } from "@/lib/queries";

const OUTCOME_MESSAGE: Record<string, (label: string | null, detail: string | null) => string> = {
  started: (label) => `지원을 시작했습니다: ${label ?? ""}`,
  duplicate: (label) => `이미 지원 이력이 있습니다: ${label ?? ""}`,
  unsupported_platform: (_label, detail) => detail ?? "지원하지 않는 플랫폼입니다.",
  not_found: (_label, detail) => detail ?? "공고를 찾을 수 없습니다.",
};

export function ApplyBar({ onStarted }: { onStarted: (applicationId: string) => void }) {
  const [url, setUrl] = useState("");
  const mutation = useApplyByUrl();

  const submit = () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    mutation.mutate(trimmed, {
      onSuccess: (res) => {
        const message = OUTCOME_MESSAGE[res.outcome]?.(res.label, res.detail) ?? res.outcome;
        if (res.outcome === "started") {
          toast.success(message);
          setUrl("");
          if (res.application_id) onStarted(res.application_id);
        } else {
          toast.warning(message);
        }
      },
      onError: (err) => toast.error(`지원 요청 실패: ${err.message}`),
    });
  };

  return (
    <div className="flex gap-2">
      <Input
        placeholder="wanted 공고 URL을 붙여넣으세요"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
      />
      <Button onClick={submit} disabled={mutation.isPending || !url.trim()}>
        {mutation.isPending ? "지원 중…" : "지원하기"}
      </Button>
    </div>
  );
}
