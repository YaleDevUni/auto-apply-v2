import { Link, useNavigate } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { FileUploadButton } from "@/components/FileUploadButton";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RESUME_ACCEPT, type DraftSummary } from "@/lib/draft-api";
import { errorMessage } from "@/lib/error-message";
import { formatDateTime } from "@/lib/format";
import { useDeleteDraft, useDrafts, useUploadResume } from "@/lib/queries";

// 첫 화면: 이력서 파일 → 추출 초안 → 검토(/onboarding/$draftId) → 확정. 파일은 문서함에 저장하지 않는다 (§A7).
export function OnboardingPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const drafts = useDrafts();
  const upload = useUploadResume();

  const onFile = (file: File) =>
    upload.mutate(file, {
      onSuccess: (draft) => void navigate({ to: "/onboarding/$draftId", params: { draftId: draft.id } }),
      onError: (err) =>
        toast.error(
          errorMessage(err, t, {
            unique_identifier_rejected: t("onboarding.filenameIdentifier"),
            unsupported_type: t("onboarding.unsupported"),
          }),
          { duration: 10_000 },
        ),
    });

  return (
    <div className="flex flex-col gap-8">
      <header>
        <h1 className="text-xl font-semibold">{t("onboarding.title")}</h1>
        <p className="text-muted-foreground text-sm">{t("onboarding.description")}</p>
      </header>

      <Card>
        <CardContent className="flex flex-col items-start gap-3">
          <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-sm">
            <li>{t("onboarding.step1")}</li>
            <li>{t("onboarding.step2")}</li>
            <li>{t("onboarding.privacy")}</li>
          </ul>
          <FileUploadButton
            accept={RESUME_ACCEPT}
            label={t("onboarding.upload")}
            pendingLabel={t("onboarding.extracting")}
            pending={upload.isPending}
            onFile={onFile}
          />
          {upload.isPending && <p className="text-muted-foreground text-xs">{t("onboarding.extractingHint")}</p>}
        </CardContent>
      </Card>

      {!drafts.isSuccess ? (
        <QueryStatus query={drafts} />
      ) : (
        drafts.data.length > 0 && (
          <section className="flex flex-col gap-3">
            <h2 className="text-base font-semibold">{t("onboarding.drafts")}</h2>
            {drafts.data.map((d) => (
              <DraftRow key={d.id} draft={d} />
            ))}
          </section>
        )
      )}

      <p className="text-sm">
        <Link to="/profile" className="text-primary underline-offset-4 hover:underline">
          {t("onboarding.skip")}
        </Link>
      </p>
    </div>
  );
}

function DraftRow({ draft }: { draft: DraftSummary }) {
  const { t, i18n } = useTranslation();
  const remove = useDeleteDraft();
  const onDelete = () => {
    if (!window.confirm(t("onboarding.review.discardConfirm"))) return;
    remove.mutate(draft.id, { onError: (err) => toast.error(errorMessage(err, t)) });
  };
  const counts = t("onboarding.draftCounts", { fields: draft.profile_field_count, experiences: draft.experience_count });
  return (
    <Card size="sm">
      <CardContent className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 flex-col text-sm">
          <p className="truncate font-medium">{draft.source_filename ?? t("onboarding.v2Source")}</p>
          <p className="text-muted-foreground text-xs">
            {formatDateTime(draft.created_at, i18n.language)} · {counts}
            {draft.redacted_identifiers > 0 && ` · ${t("onboarding.redactedShort", { n: draft.redacted_identifiers })}`}
          </p>
        </div>
        <div className="flex shrink-0 gap-1">
          <Link
            to="/onboarding/$draftId"
            params={{ draftId: draft.id }}
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            {t("onboarding.resume")}
          </Link>
          <Button variant="ghost" size="icon" aria-label={t("onboarding.review.discardDraft")} onClick={onDelete} disabled={remove.isPending}>
            <Trash2 />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
