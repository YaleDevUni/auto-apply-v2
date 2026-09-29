import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Field, NativeSelect } from "@/components/form";
import { Input } from "@/components/ui/input";
import {
  additionalProgress,
  type AdditionalDraft,
  type TriState,
} from "@/features/profile/profile-form";
import { MILITARY_STATUSES, type MilitaryStatus } from "@/lib/profile-api";

type TextKey = "militaryBranch" | "militaryRank" | "militaryPeriod" | "militaryNote" | "desiredSalary" | "availableFrom" | "residence";

// 병역·보훈 등 한국 지원서 특유 항목. 전부 선택이고 기본은 접혀 있다 — 비워두면 실행 중 ask_user (D10).
export function AdditionalInfoSection({
  value,
  onChange,
}: {
  value: AdditionalDraft;
  onChange: (next: AdditionalDraft) => void;
}) {
  const { t } = useTranslation();
  const set = <K extends keyof AdditionalDraft>(key: K, v: AdditionalDraft[K]) =>
    onChange({ ...value, [key]: v });

  const text = (key: TextKey, label: string, placeholder?: string) => (
    <Field label={label} htmlFor={`additional-${key}`}>
      <Input
        id={`additional-${key}`}
        value={value[key]}
        placeholder={placeholder}
        onChange={(e) => set(key, e.target.value)}
      />
    </Field>
  );

  const tri = (key: "veteran" | "disability", label: string) => (
    <Field label={label} htmlFor={`additional-${key}`}>
      <NativeSelect
        id={`additional-${key}`}
        value={value[key]}
        onChange={(e) => set(key, e.target.value as TriState)}
      >
        <option value="">{t("profile.additional.unset")}</option>
        <option value="yes">{t("profile.additional.yes")}</option>
        <option value="no">{t("profile.additional.no")}</option>
      </NativeSelect>
    </Field>
  );

  const { filled, total } = additionalProgress(value);

  return (
    <details className="group rounded-xl border" data-testid="additional-info">
      <summary className="flex cursor-pointer list-none items-center gap-2 p-4 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 transition-transform group-open:rotate-90" />
        <span className="font-semibold">{t("profile.additional.title")}</span>
        <span className="text-muted-foreground text-sm">({filled}/{total})</span>
      </summary>
      <div className="flex flex-col gap-4 border-t p-4">
        <p className="bg-muted text-muted-foreground rounded-lg p-3 text-sm">
          {t("profile.additional.hint")}
        </p>

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 text-sm font-semibold">{t("profile.additional.military.title")}</legend>
          <Field label={t("profile.additional.military.status")} htmlFor="additional-militaryStatus">
            <NativeSelect
              id="additional-militaryStatus"
              value={value.militaryStatus}
              onChange={(e) => set("militaryStatus", e.target.value as MilitaryStatus | "")}
            >
              <option value="">{t("profile.additional.unset")}</option>
              {MILITARY_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {t(`profile.additional.military.statuses.${s}`)}
                </option>
              ))}
            </NativeSelect>
          </Field>
          {value.militaryStatus !== "" && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {text("militaryBranch", t("profile.additional.military.branch"))}
              {text("militaryRank", t("profile.additional.military.rank"))}
              {text("militaryPeriod", t("profile.additional.military.period"))}
              {text("militaryNote", t("profile.additional.military.note"))}
            </div>
          )}
        </fieldset>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {tri("veteran", t("profile.additional.veteran"))}
          {tri("disability", t("profile.additional.disability"))}
          {text("desiredSalary", t("profile.additional.desiredSalary"), t("profile.additional.desiredSalaryPlaceholder"))}
          {text("availableFrom", t("profile.additional.availableFrom"), t("profile.additional.availableFromPlaceholder"))}
          {text("residence", t("profile.additional.residence"))}
        </div>
      </div>
    </details>
  );
}
