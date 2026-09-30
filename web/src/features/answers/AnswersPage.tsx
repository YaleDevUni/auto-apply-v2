import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Field } from "@/components/form";
import { QueryStatus } from "@/components/QueryStatus";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/error-message";
import { formatDateTime } from "@/lib/format";
import type { Answer } from "@/lib/knowledge-api";
import { useAnswers, useDeleteAnswer, useSaveAnswer } from "@/lib/queries";
import { useUnsavedGuard } from "@/lib/use-unsaved-guard";

interface Editing {
  target: Answer | null; // null = 새 답변
  question: string;
  answer: string;
}

export function AnswersPage() {
  const { t, i18n } = useTranslation();
  const query = useAnswers();
  const remove = useDeleteAnswer();
  const [editing, setEditing] = useState<Editing | null>(null);
  const [filter, setFilter] = useState("");

  const dirty =
    editing !== null &&
    (editing.question !== (editing.target?.question_key ?? "") || editing.answer !== (editing.target?.answer ?? ""));
  useUnsavedGuard(dirty);

  const open = (target: Answer | null) => {
    if (dirty && !window.confirm(t("common.unsavedLeave"))) return;
    setEditing({ target, question: target?.question_key ?? "", answer: target?.answer ?? "" });
  };

  const onDelete = (a: Answer) => {
    if (!window.confirm(t("answers.deleteConfirm", { question: a.question_key }))) return;
    remove.mutate(a.id, {
      onSuccess: () => toast.success(t("common.deleted")),
      onError: (err) => toast.error(errorMessage(err, t)),
    });
  };

  if (!query.isSuccess) return <QueryStatus query={query} />;
  const needle = filter.trim().toLowerCase();
  const rows = [...query.data]
    .sort((a, b) => a.question_key.localeCompare(b.question_key, i18n.language))
    .filter((a) => !needle || `${a.question_key}\n${a.answer}`.toLowerCase().includes(needle));
  const editor = (target: Answer | null) =>
    editing && editing.target?.id === target?.id ? (
      <AnswerEditor key={target?.id ?? "new"} editing={editing} onChange={setEditing} onClose={() => setEditing(null)} />
    ) : null;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{t("answers.title")}</h1>
          <p className="text-muted-foreground text-sm">{t("answers.description")}</p>
        </div>
        <Button onClick={() => open(null)} disabled={editing !== null && editing.target === null}>
          <Plus />
          {t("answers.add")}
        </Button>
      </header>

      {editor(null)}

      {query.data.length > 0 && (
        <Input
          aria-label={t("answers.filter")}
          placeholder={t("answers.filter")}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      )}
      {query.data.length === 0 && editing === null && <p className="text-muted-foreground text-sm">{t("answers.empty")}</p>}

      <div className="flex flex-col gap-3">
        {rows.map(
          (a) =>
            editor(a) ?? (
              <Card key={a.id} size="sm">
                <CardContent className="flex items-start justify-between gap-4">
                  <div className="flex min-w-0 flex-col gap-1 text-sm">
                    <p className="font-semibold">{a.question_key}</p>
                    <p className="whitespace-pre-wrap">{a.answer}</p>
                    <p className="text-muted-foreground text-xs">
                      {t("answers.updatedAt", { at: formatDateTime(a.updated_at, i18n.language) })}
                      {a.source_application_id && ` · ${t("answers.source", { id: a.source_application_id })}`}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button variant="ghost" size="icon" aria-label={`${a.question_key} ${t("common.edit")}`} onClick={() => open(a)}>
                      <Pencil />
                    </Button>
                    <Button variant="ghost" size="icon" aria-label={`${a.question_key} ${t("common.remove")}`} onClick={() => onDelete(a)}>
                      <Trash2 />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ),
        )}
      </div>
    </div>
  );
}

function AnswerEditor({
  editing,
  onChange,
  onClose,
}: {
  editing: Editing;
  onChange: (next: Editing) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const save = useSaveAnswer();
  const [missing, setMissing] = useState(false);
  const id = editing.target?.id ?? "new";

  const submit = () => {
    const question = editing.question.trim();
    const answer = editing.answer.trim();
    setMissing(!question || !answer);
    if (!question || !answer) return;
    save.mutate(
      { id: editing.target?.id ?? null, body: { question, answer, source_application_id: editing.target?.source_application_id ?? null } },
      {
        onSuccess: () => {
          toast.success(t("common.saved"));
          onClose();
        },
        onError: (err) => toast.error(errorMessage(err, t, { conflict: t("answers.conflict") })),
      },
    );
  };

  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        <Field label={t("answers.question")} hint={t("answers.questionHint")} htmlFor={`answer-${id}-q`}>
          <Input id={`answer-${id}-q`} value={editing.question} onChange={(e) => onChange({ ...editing, question: e.target.value })} />
        </Field>
        <Field label={t("answers.answer")} htmlFor={`answer-${id}-a`} error={missing ? t("answers.required") : undefined}>
          <Textarea id={`answer-${id}-a`} value={editing.answer} onChange={(e) => onChange({ ...editing, answer: e.target.value })} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} disabled={save.isPending}>
            {save.isPending ? t("common.saving") : t("common.save")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
