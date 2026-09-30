import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Field, NativeSelect, Section } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  emptySection,
  type ExperienceErrors,
  type ExperienceForm,
  type SectionForm,
} from "@/features/experiences/experience-form";
import { FactListEditor } from "@/features/experiences/FactListEditor";
import { EXPERIENCE_KINDS, type DocumentMeta, type ExperienceKind } from "@/lib/knowledge-api";

// 저장된 경험 편집과 온보딩 초안 검토가 같이 쓴다. `documents` 를 안 주면 첨부 칸을 숨긴다(초안엔 첨부가 없다).
export function ExperienceEditor({
  id,
  value,
  onChange,
  errors,
  documents,
}: {
  id: string;
  value: ExperienceForm;
  onChange: (next: ExperienceForm) => void;
  errors: ExperienceErrors | null;
  documents?: DocumentMeta[];
}) {
  const { t } = useTranslation();
  const set = <K extends keyof ExperienceForm>(key: K, v: ExperienceForm[K]) => onChange({ ...value, [key]: v });
  const setSection = (index: number, patch: Partial<SectionForm>) =>
    set("sections", value.sections.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  const text = (key: "role" | "period" | "url", label: string, placeholder?: string) => (
    <Field label={label} htmlFor={`${id}-${key}`}>
      <Input id={`${id}-${key}`} value={value[key]} placeholder={placeholder} onChange={(e) => set(key, e.target.value)} />
    </Field>
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={t("experiences.kind")} htmlFor={`${id}-kind`}>
          <NativeSelect id={`${id}-kind`} value={value.kind} onChange={(e) => set("kind", e.target.value as ExperienceKind)}>
            {EXPERIENCE_KINDS.map((k) => (
              <option key={k} value={k}>
                {t(`experiences.kinds.${k}`)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field
          label={t("experiences.name")}
          htmlFor={`${id}-name`}
          error={errors?.name ? t("experiences.nameRequired") : undefined}
        >
          <Input
            id={`${id}-name`}
            value={value.name}
            aria-invalid={errors?.name || undefined}
            onChange={(e) => set("name", e.target.value)}
          />
        </Field>
        {text("role", t("experiences.role"))}
        {text("period", t("experiences.period"), t("profile.education.periodPlaceholder"))}
        {text("url", t("experiences.url"), "https://")}
        <Field label={t("experiences.skills")} hint={t("profile.skills.hint")} htmlFor={`${id}-skills`}>
          <Input id={`${id}-skills`} value={value.skillsText} onChange={(e) => set("skillsText", e.target.value)} />
        </Field>
      </div>

      <Section title={t("experiences.facts.title")} description={t("experiences.facts.hint")}>
        <FactListEditor id={`${id}-facts`} facts={value.facts} onChange={(facts) => set("facts", facts)} />
      </Section>

      <Section title={t("experiences.sections.title")} description={t("experiences.sections.hint")}>
        {value.sections.map((section, index) => (
          <fieldset key={section.key ?? `new-${index}`} className="flex flex-col gap-3 rounded-lg border p-3">
            <div className="flex items-end gap-2">
              <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2">
                <Field
                  label={t("experiences.sections.name")}
                  htmlFor={`${id}-section-${index}-title`}
                  error={errors?.sectionTitles.includes(index) ? t("experiences.sections.nameRequired") : undefined}
                >
                  <Input
                    id={`${id}-section-${index}-title`}
                    value={section.title}
                    aria-invalid={errors?.sectionTitles.includes(index) || undefined}
                    onChange={(e) => setSection(index, { title: e.target.value })}
                  />
                </Field>
                <Field label={t("experiences.period")} htmlFor={`${id}-section-${index}-period`}>
                  <Input
                    id={`${id}-section-${index}-period`}
                    value={section.period}
                    onChange={(e) => setSection(index, { period: e.target.value })}
                  />
                </Field>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`${t("experiences.sections.name")} ${index + 1} ${t("common.remove")}`}
                onClick={() => set("sections", value.sections.filter((_, i) => i !== index))}
              >
                <Trash2 />
              </Button>
            </div>
            <FactListEditor
              id={`${id}-section-${index}-facts`}
              facts={section.facts}
              onChange={(facts) => setSection(index, { facts })}
            />
          </fieldset>
        ))}
        <div>
          <Button type="button" variant="outline" size="sm" onClick={() => set("sections", [...value.sections, emptySection()])}>
            <Plus />
            {t("experiences.sections.add")}
          </Button>
        </div>
      </Section>

      {documents && (
        <Section title={t("experiences.documents.title")} description={t("experiences.documents.hint")}>
          {documents.length === 0 ? (
            <p className="text-muted-foreground text-sm">{t("experiences.documents.none")}</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {documents.map((doc) => (
                <label key={doc.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={value.documentIds.includes(doc.id)}
                    onChange={(e) =>
                      set(
                        "documentIds",
                        e.target.checked
                          ? [...value.documentIds, doc.id]
                          : value.documentIds.filter((d) => d !== doc.id),
                      )
                    }
                  />
                  {doc.filename}
                </label>
              ))}
            </div>
          )}
        </Section>
      )}
    </div>
  );
}
