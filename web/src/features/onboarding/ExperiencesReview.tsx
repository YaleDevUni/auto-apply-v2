import { useTranslation } from "react-i18next";
import { Section } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ExperienceEditor } from "@/features/experiences/ExperienceEditor";
import { toExtracted, type ExperienceErrors, type ExperienceForm } from "@/features/experiences/experience-form";
import { ExperienceSummary } from "@/features/experiences/ExperienceSummary";
import { ReviewToggle } from "@/features/onboarding/ReviewToggle";

// 초안의 경험 목록 — 항목마다 채택/버림 + "수정"으로 펼치는 편집기(저장된 경험과 같은 편집기, 첨부 칸만 없다).
export function ExperiencesReview({
  items,
  onItemsChange,
  keep,
  onKeepChange,
  open,
  onOpenChange,
  errors,
}: {
  items: ExperienceForm[];
  onItemsChange: (next: ExperienceForm[]) => void;
  keep: boolean[];
  onKeepChange: (next: boolean[]) => void;
  open: Set<number>;
  onOpenChange: (next: Set<number>) => void;
  errors: (ExperienceErrors | null)[];
}) {
  const { t } = useTranslation();
  const toggleOpen = (i: number) => {
    const next = new Set(open);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    onOpenChange(next);
  };

  return (
    <Section title={t("experiences.title")} description={t("onboarding.review.experiencesHint")}>
      {items.length === 0 && <p className="text-muted-foreground text-sm">{t("onboarding.review.noExperiences")}</p>}
      {items.map((item, i) => (
        <ReviewToggle
          key={i}
          label={item.name || t("experiences.unnamed")}
          checked={keep[i]}
          onChange={(on) => onKeepChange(keep.map((k, j) => (j === i ? on : k)))}
        >
          <Card size="sm">
            <CardContent className="flex flex-col gap-4">
              <div className="flex items-start justify-between gap-4">
                <ExperienceSummary data={toExtracted(item)} extra={t(`experiences.kinds.${item.kind}`)} />
                <Button variant="outline" size="sm" onClick={() => toggleOpen(i)}>
                  {open.has(i) ? t("onboarding.review.closeEdit") : t("common.edit")}
                </Button>
              </div>
              {errors[i] && <p className="text-destructive text-xs">{t("onboarding.review.experienceInvalid")}</p>}
              {open.has(i) && (
                <ExperienceEditor
                  id={`draft-exp-${i}`}
                  value={item}
                  errors={errors[i]}
                  onChange={(next) => onItemsChange(items.map((x, j) => (j === i ? next : x)))}
                />
              )}
            </CardContent>
          </Card>
        </ReviewToggle>
      ))}
    </Section>
  );
}
