import { useNavigate, useParams } from "@tanstack/react-router";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { QueryStatus } from "@/components/QueryStatus";
import { Button } from "@/components/ui/button";
import { ExperiencesReview } from "@/features/onboarding/ExperiencesReview";
import { ProfileFieldsReview } from "@/features/onboarding/ProfileFieldsReview";
import {
  hasProblems,
  initialReview,
  reviewProblems,
  toContent,
  toSelection,
  type ReviewState,
} from "@/features/onboarding/review-model";
import type { ExtractionDraft } from "@/lib/draft-api";
import { errorMessage } from "@/lib/error-message";
import { useConfirmDraft, useDeleteDraft, useDraft, useProfile, useSaveDraft } from "@/lib/queries";
import { useUnsavedGuard } from "@/lib/use-unsaved-guard";

export function DraftReviewPage() {
  const { draftId } = useParams({ from: "/onboarding/$draftId" });
  const draft = useDraft(draftId);
  const profile = useProfile();
  if (!draft.isSuccess) return <QueryStatus query={draft} />;
  if (!profile.isSuccess) return <QueryStatus query={profile} />;
  return <Review key={draft.data.id} draft={draft.data} currentName={profile.data?.name ?? null} />;
}

function Review({ draft, currentName }: { draft: ExtractionDraft; currentName: string | null }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const save = useSaveDraft();
  const confirm = useConfirmDraft();
  const remove = useDeleteDraft();
  const [state, setState] = useState<ReviewState>(() => initialReview(draft));
  const [baseline, setBaseline] = useState(() => JSON.stringify(toContent(state)));
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [attempted, setAttempted] = useState(false);

  const dirty = JSON.stringify(toContent(state)) !== baseline;
  const allowLeave = useUnsavedGuard(dirty);
  const problems = reviewProblems(state, currentName);
  const busy = save.isPending || confirm.isPending || remove.isPending;
  const set = <K extends keyof ReviewState>(key: K, v: ReviewState[K]) => setState((s) => ({ ...s, [key]: v }));

  const saveOnly = () => {
    const content = toContent(state);
    save.mutate(
      { id: draft.id, content },
      {
        onSuccess: () => {
          setBaseline(JSON.stringify(content));
          toast.success(t("common.saved"));
        },
        onError: (err) => toast.error(errorMessage(err, t)),
      },
    );
  };

  const submit = async () => {
    setAttempted(true);
    if (hasProblems(problems)) {
      // 고칠 곳이 있는 경험은 편집기를 펼쳐 칸 에러가 보이게 한다.
      setOpen(new Set([...open, ...problems.experiences.flatMap((e, i) => (e ? [i] : []))]));
      return;
    }
    try {
      // 고친 내용을 먼저 초안에 저장해야 confirm 이 그 값을 병합한다 (서버는 초안 내용을 병합한다).
      await save.mutateAsync({ id: draft.id, content: toContent(state) });
      const selection = toSelection(state);
      const result = await confirm.mutateAsync({ id: draft.id, selection });
      allowLeave();
      toast.success(
        t("onboarding.review.confirmed", { fields: selection.profile_fields.length, experiences: result.experiences.length }),
      );
      void navigate({ to: result.profile ? "/profile" : "/experiences" });
    } catch (err) {
      toast.error(errorMessage(err, t));
    }
  };

  const discard = () => {
    if (!window.confirm(t("onboarding.review.discardConfirm"))) return;
    remove.mutate(draft.id, {
      onSuccess: () => {
        allowLeave();
        void navigate({ to: "/onboarding" });
      },
      onError: (err) => toast.error(errorMessage(err, t)),
    });
  };

  return (
    <div className="flex flex-col gap-8">
      <header className="bg-background sticky top-0 z-10 flex flex-wrap items-start justify-between gap-4 py-2">
        <div>
          <h1 className="text-xl font-semibold">{t("onboarding.review.title")}</h1>
          <p className="text-muted-foreground text-sm">
            {draft.source_filename ?? t("onboarding.v2Source")} · {t("onboarding.review.description")}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={discard} disabled={busy}>
            {t("onboarding.review.discardDraft")}
          </Button>
          <Button variant="outline" onClick={saveOnly} disabled={busy || !dirty}>
            {t("onboarding.review.saveProgress")}
          </Button>
          <Button onClick={() => void submit()} disabled={busy || problems.nothingSelected}>
            {confirm.isPending ? t("onboarding.review.confirming") : t("onboarding.review.confirm")}
          </Button>
        </div>
      </header>

      {draft.redacted_identifiers > 0 && (
        <p className="bg-muted flex items-start gap-2 rounded-lg p-3 text-sm" role="status">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" />
          {t("onboarding.redacted", { n: draft.redacted_identifiers })}
        </p>
      )}
      {problems.nothingSelected && <p className="text-muted-foreground text-sm">{t("onboarding.review.nothingSelected")}</p>}
      {attempted && problems.nameMissing && (
        <p className="text-destructive text-sm" role="alert">
          {t("onboarding.review.nameMissing")}
        </p>
      )}

      <ProfileFieldsReview
        value={state.profile}
        onChange={(p) => set("profile", p)}
        fields={state.fields}
        onFieldsChange={(f) => set("fields", f)}
        nameMissing={attempted && problems.nameMissing}
      />
      <ExperiencesReview
        items={state.experiences}
        onItemsChange={(items) => set("experiences", items)}
        keep={state.keep}
        onKeepChange={(keep) => set("keep", keep)}
        open={open}
        onOpenChange={setOpen}
        errors={attempted ? problems.experiences : []}
      />
    </div>
  );
}
