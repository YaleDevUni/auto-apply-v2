import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { FileUploadButton } from "@/components/FileUploadButton";
import { QueryStatus } from "@/components/QueryStatus";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { API_BASE_URL } from "@/lib/api";
import { errorMessage } from "@/lib/error-message";
import { formatBytes, formatDateTime } from "@/lib/format";
import { DOCUMENT_ACCEPT, documentApi, type DocumentMeta } from "@/lib/knowledge-api";
import { useDeleteDocument, useDocuments, useUploadDocument } from "@/lib/queries";

const TYPE_LABELS: Record<string, string> = {
  "application/pdf": "PDF",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
  "image/png": "PNG",
  "image/jpeg": "JPG",
};

export function DocumentsPage() {
  const { t, i18n } = useTranslation();
  const query = useDocuments();
  const upload = useUploadDocument();
  const remove = useDeleteDocument();

  const onFile = (file: File) =>
    upload.mutate(file, {
      onSuccess: (doc) => toast.success(t("documents.uploaded", { name: doc.filename })),
      // 고정 파일은 사이트에 그대로 내는 원본이라 서버가 가리지 않고 거부한다 (§A7) — 그 사정을 알려 준다.
      onError: (err) =>
        toast.error(
          errorMessage(err, t, {
            unique_identifier_rejected: t("documents.identifierRejected"),
            validation_error: t("documents.invalidUpload"),
          }),
          { duration: 10_000 },
        ),
    });

  const onDelete = (doc: DocumentMeta) => {
    if (!window.confirm(t("documents.deleteConfirm", { name: doc.filename }))) return;
    remove.mutate(doc.id, {
      onSuccess: () => toast.success(t("common.deleted")),
      onError: (err) => toast.error(errorMessage(err, t)),
    });
  };

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{t("documents.title")}</h1>
          <p className="text-muted-foreground text-sm">{t("documents.description")}</p>
        </div>
        <FileUploadButton
          accept={DOCUMENT_ACCEPT}
          label={t("documents.upload")}
          pendingLabel={t("documents.uploading")}
          pending={upload.isPending}
          onFile={onFile}
        />
      </header>
      <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
        <li>{t("documents.rules")}</li>
        <li>{t("documents.identifierRule")}</li>
        <li>{t("documents.imageLimit")}</li>
      </ul>

      {!query.isSuccess ? (
        <QueryStatus query={query} />
      ) : query.data.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("documents.empty")}</p>
      ) : (
        <div className="flex flex-col gap-2">
          {query.data.map((doc) => (
            <Card key={doc.id} size="sm">
              <CardContent className="flex items-center justify-between gap-4">
                <div className="flex min-w-0 flex-col text-sm">
                  <a
                    href={`${API_BASE_URL}${documentApi.contentPath(doc.id)}`}
                    target="_blank"
                    rel="noreferrer"
                    className="truncate font-medium underline-offset-4 hover:underline"
                  >
                    {doc.filename}
                  </a>
                  <p className="text-muted-foreground text-xs">
                    {[TYPE_LABELS[doc.content_type] ?? doc.content_type, formatBytes(doc.size_bytes), formatDateTime(doc.created_at, i18n.language)].join(" · ")}
                  </p>
                </div>
                <Button variant="ghost" size="icon" aria-label={`${doc.filename} ${t("common.remove")}`} onClick={() => onDelete(doc)}>
                  <Trash2 />
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
