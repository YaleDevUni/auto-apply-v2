import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Field } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { emptyFact, type FactForm } from "@/features/experiences/experience-form";

// fact = 이력서에 인용될 수 있는 서술 한 줄 (절대 규칙 4 의 근거 단위).
export function FactListEditor({
  id,
  facts,
  onChange,
}: {
  id: string;
  facts: FactForm[];
  onChange: (facts: FactForm[]) => void;
}) {
  const { t } = useTranslation();
  const update = (index: number, patch: Partial<FactForm>) =>
    onChange(facts.map((f, i) => (i === index ? { ...f, ...patch } : f)));

  return (
    <div className="flex flex-col gap-3">
      {facts.map((fact, index) => (
        <div key={fact.id ?? `new-${index}`} className="flex items-start gap-2">
          <div className="flex flex-1 flex-col gap-2">
            <Field label={`${t("experiences.fact.text")} ${index + 1}`} htmlFor={`${id}-${index}-text`}>
              <Textarea
                id={`${id}-${index}-text`}
                value={fact.text}
                placeholder={t("experiences.fact.textPlaceholder")}
                onChange={(e) => update(index, { text: e.target.value })}
              />
            </Field>
            <Field label={t("experiences.fact.skills")} htmlFor={`${id}-${index}-skills`}>
              <Input
                id={`${id}-${index}-skills`}
                value={fact.skillsText}
                placeholder={t("experiences.fact.skillsPlaceholder")}
                onChange={(e) => update(index, { skillsText: e.target.value })}
              />
            </Field>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="mt-6"
            aria-label={`${t("experiences.fact.text")} ${index + 1} ${t("common.remove")}`}
            onClick={() => onChange(facts.filter((_, i) => i !== index))}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <div>
        <Button type="button" variant="outline" size="sm" onClick={() => onChange([...facts, emptyFact()])}>
          <Plus />
          {t("experiences.fact.add")}
        </Button>
      </div>
    </div>
  );
}
