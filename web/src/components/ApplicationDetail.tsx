import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { StateBadge } from "@/components/StateBadge";
import type { ApplicationListItem, RevisionScope } from "@/lib/api";
import { TERMINAL_STATES } from "@/lib/api";
import {
  useApplication,
  useApprove,
  useCancel,
  usePendingDecision,
  useReject,
  useRevise,
} from "@/lib/queries";
import { PROGRESS_STEPS } from "@/lib/state-labels";

export function ApplicationDetail({
  applicationId,
  listItem,
}: {
  applicationId: string;
  listItem: ApplicationListItem | undefined;
}) {
  const { data: application } = useApplication(applicationId);
  const { data: pending } = usePendingDecision(applicationId);
  const approve = useApprove(applicationId);
  const reject = useReject(applicationId);
  const revise = useRevise(applicationId);
  const cancel = useCancel(applicationId);

  const [rejectReason, setRejectReason] = useState("");
  const [reviseFeedback, setReviseFeedback] = useState("");
  const [reviseScope, setReviseScope] = useState<RevisionScope>("specific");

  if (!application) {
    return <p className="text-muted-foreground p-4 text-sm">불러오는 중…</p>;
  }

  const state = application.state;
  const isTerminal = TERMINAL_STATES.has(state);
  const isAwaitingApproval = state === "awaiting_approval";
  const stepIndex = PROGRESS_STEPS.findIndex((s) => s.state === state);
  // history 는 상태 전이 로그다 — 종결 사유(거절/실패 이유)는 가장 최근 행에 실려 있다.
  const latestReason = application.history.at(-1)?.reason;

  return (
    <div className="flex flex-col gap-4 overflow-y-auto p-1">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>
              {listItem?.company ?? application.application_id}
              {listItem?.title ? ` · ${listItem.title}` : ""}
            </CardTitle>
            <StateBadge state={state} />
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {(listItem?.job_url ?? pending?.job_url) && (
            <a
              href={listItem?.job_url ?? pending?.job_url ?? undefined}
              target="_blank"
              rel="noreferrer"
              className="text-primary text-sm underline"
            >
              공고 원문 보기
            </a>
          )}

          {!isTerminal && stepIndex >= 0 && (
            <ol className="flex flex-wrap gap-2 text-xs">
              {PROGRESS_STEPS.map((step, i) => (
                <li
                  key={step.state}
                  className={
                    i <= stepIndex
                      ? "text-foreground font-medium"
                      : "text-muted-foreground"
                  }
                >
                  {step.label}
                  {i < PROGRESS_STEPS.length - 1 && <span className="mx-1">→</span>}
                </li>
              ))}
            </ol>
          )}

          {latestReason && (
            <p className="text-muted-foreground text-sm">사유: {latestReason}</p>
          )}
        </CardContent>
      </Card>

      {pending?.has_pending && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">이력서 / 승인</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-1">
              {pending.mode && <Badge variant="secondary">모드: {pending.mode}</Badge>}
              {pending.caution_documents.map((doc) => (
                <Badge key={doc} variant="outline">
                  ⚠️ {doc}
                </Badge>
              ))}
              {pending.portfolio_filename && (
                <Badge variant="outline">첨부: {pending.portfolio_filename}</Badge>
              )}
            </div>
            {pending.caution_notes.length > 0 && (
              <ul className="text-muted-foreground list-inside list-disc text-sm">
                {pending.caution_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            )}

            {pending.resume_url ? (
              <iframe
                title="이력서 PDF"
                src={pending.resume_url}
                className="h-[500px] w-full rounded-md border"
              />
            ) : (
              <p className="text-muted-foreground text-sm">PDF를 불러올 수 없습니다.</p>
            )}

            <div className="flex gap-2">
              <Button
                disabled={!isAwaitingApproval || approve.isPending}
                onClick={() =>
                  approve.mutate(undefined, {
                    onSuccess: () => toast.success("승인했습니다."),
                    onError: (e) => toast.error(`승인 실패: ${e.message}`),
                  })
                }
              >
                승인
              </Button>
              <Button
                variant="destructive"
                disabled={!isAwaitingApproval || reject.isPending}
                onClick={() =>
                  reject.mutate(rejectReason, {
                    onSuccess: () => {
                      toast.success("거절했습니다.");
                      setRejectReason("");
                    },
                    onError: (e) => toast.error(`거절 실패: ${e.message}`),
                  })
                }
              >
                거절
              </Button>
            </div>
            <Textarea
              placeholder="거절 사유 (선택)"
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              disabled={!isAwaitingApproval}
            />

            <Separator />

            <div className="flex flex-col gap-2">
              <p className="text-sm font-medium">수정하기</p>
              <Textarea
                placeholder="이력서에 반영할 피드백을 적어주세요"
                value={reviseFeedback}
                onChange={(e) => setReviseFeedback(e.target.value)}
                disabled={!isAwaitingApproval}
              />
              <RadioGroup
                value={reviseScope}
                onValueChange={(v) => setReviseScope(v as RevisionScope)}
                className="flex gap-4"
              >
                <label className="flex items-center gap-2 text-sm">
                  <RadioGroupItem value="specific" disabled={!isAwaitingApproval} />
                  이번만
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <RadioGroupItem value="general" disabled={!isAwaitingApproval} />
                  항상 (가이드에 반영)
                </label>
              </RadioGroup>
              <Button
                variant="secondary"
                disabled={!isAwaitingApproval || !reviseFeedback.trim() || revise.isPending}
                onClick={() =>
                  revise.mutate(
                    { feedback: reviseFeedback, scope: reviseScope },
                    {
                      onSuccess: () => {
                        toast.success("수정요청을 보냈습니다.");
                        setReviseFeedback("");
                      },
                      onError: (e) => toast.error(`수정요청 실패: ${e.message}`),
                    },
                  )
                }
              >
                수정요청 보내기
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Button
        variant="outline"
        disabled={isTerminal || cancel.isPending}
        onClick={() =>
          cancel.mutate(undefined, {
            onSuccess: () => toast.success("취소했습니다."),
            onError: (e) => toast.error(`취소 실패: ${e.message}`),
          })
        }
      >
        지원 취소
      </Button>
    </div>
  );
}
