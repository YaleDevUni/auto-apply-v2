import { Link } from "@tanstack/react-router";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { QueryStatus } from "@/components/QueryStatus";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ExperienceEditor } from "@/features/experiences/ExperienceEditor";
import {
  emptyExperience,
  factCount,
  fromExperience,
  toExperienceBody,
  validateExperience,
  type ExperienceErrors,
  type ExperienceForm,
} from "@/features/experiences/experience-form";
import { ExperienceSummary } from "@/features/experiences/ExperienceSummary";
import { errorMessage } from "@/lib/error-message";
import { EXPERIENCE_KINDS, type Experience } from "@/lib/knowledge-api";
import { useDeleteExperience, useDocuments, useExperiences, useSaveExperience } from "@/lib/queries";
import { useUnsavedGuard } from "@/lib/use-unsaved-guard";

// 편집은 한 번에 하나 — 새 경험(id null) 또는 기존 경험 하나.
interface Editing {
  id: string | null;
  form: ExperienceForm;
  baseline: string;
}

const snapshot = (form: ExperienceForm) => JSON.stringify(toExperienceBody(form));

export function ExperiencesPage() {
  const { t } = useTranslation();
  const query = useExperiences();
  const remove = useDeleteExperience();
  const [editing, setEditing] = useState<Editing | null>(null);

  const dirty = editing !== null && snapshot(editing.form) !== editing.baseline;
  useUnsavedGuard(dirty);

  const open = (id: string | null, form: ExperienceForm) => {
    if (dirty && !window.confirm(t("common.unsavedLeave"))) return;
    setEditing({ id, form, baseline: snapshot(form) });
  };

  const onDelete = (exp: Experience) => {
    if (!window.confirm(t("experiences.deleteConfirm", { name: exp.name }))) return;
    remove.mutate(exp.id, {
      onSuccess: () => toast.success(t("common.deleted")),
      onError: (err) => toast.error(errorMessage(err, t)),
    });
  };

  if (!query.isSuccess) return <QueryStatus query={query} />;
  const editor = (id: string | null) =>
    editing?.id === id ? (
      <EditPanel key={id ?? "new"} editing={editing} onChange={(form) => setEditing({ ...editing, form })} onClose={() => setEditing(null)} />
    ) : null;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{t("experiences.title")}</h1>
          <p className="text-muted-foreground text-sm">{t("experiences.description")}</p>
        </div>
        <Button onClick={() => open(null, emptyExperience())} disabled={editing?.id === null}>
          <Plus />
          {t("experiences.add")}
        </Button>
      </header>

      {editor(null)}

      {query.data.length === 0 && editing === null && (
        <p className="text-muted-foreground text-sm">
          {t("experiences.empty")}{" "}
          <Link to="/onboarding" className="text-primary font-medium underline-offset-4 hover:underline">
            {t("profile.startWithResume")}
          </Link>
        </p>
      )}

      {EXPERIENCE_KINDS.map((kind) => {
        const items = query.data.filter((e) => e.kind === kind);
        if (items.length === 0) return null;
        return (
          <section key={kind} className="flex flex-col gap-3">
            <h2 className="text-base font-semibold">
              {t(`experiences.kinds.${kind}`)} <span className="text-muted-foreground font-normal">{items.length}</span>
            </h2>
            {items.map((exp) =>
              editor(exp.id) ?? (
                <Card key={exp.id} size="sm">
                  <CardContent className="flex items-start justify-between gap-4">
                    <ExperienceSummary
                      data={exp}
                      extra={t("experiences.counts", { facts: factCount(exp), documents: exp.document_ids.length })}
                    />
                    <div className="flex shrink-0 gap-1">
                      <Button variant="ghost" size="icon" aria-label={`${exp.name} ${t("common.edit")}`} onClick={() => open(exp.id, fromExperience(exp))}>
                        <Pencil />
                      </Button>
                      <Button variant="ghost" size="icon" aria-label={`${exp.name} ${t("common.remove")}`} onClick={() => onDelete(exp)}>
                        <Trash2 />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ),
            )}
          </section>
        );
      })}
    </div>
  );
}

function EditPanel({
  editing,
  onChange,
  onClose,
}: {
  editing: Editing;
  onChange: (form: ExperienceForm) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const save = useSaveExperience();
  const documents = useDocuments();
  const [errors, setErrors] = useState<ExperienceErrors | null>(null);

  const submit = () => {
    const found = validateExperience(editing.form);
    setErrors(found);
    if (found) return;
    save.mutate(
      { id: editing.id, body: toExperienceBody(editing.form) },
      {
        onSuccess: () => {
          toast.success(t("common.saved"));
          onClose();
        },
        onError: (err) => toast.error(errorMessage(err, t)),
      },
    );
  };

  return (
    <Card>
      <CardContent className="flex flex-col gap-6">
        <ExperienceEditor
          id={`exp-${editing.id ?? "new"}`}
          value={editing.form}
          onChange={onChange}
          errors={errors}
          documents={documents.data ?? []}
        />
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
