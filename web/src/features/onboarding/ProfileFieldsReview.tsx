import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Field, Section } from "@/components/form";
import { Input } from "@/components/ui/input";
import { ReviewToggle } from "@/features/onboarding/ReviewToggle";
import { AdditionalInfoSection } from "@/features/profile/AdditionalInfoSection";
import { ListEditor } from "@/features/profile/ListEditor";
import type { ProfileDraft } from "@/features/profile/profile-form";
import type { ProfileField } from "@/lib/draft-api";

// 초안의 인적사항 — 인적사항 화면과 같은 양식 조각을 쓰고, 서버 ProfileField 단위로 채택/버림을 고른다.
export function ProfileFieldsReview({
  value,
  onChange,
  fields,
  onFieldsChange,
  nameMissing,
}: {
  value: ProfileDraft;
  onChange: (next: ProfileDraft) => void;
  fields: Record<ProfileField, boolean>;
  onFieldsChange: (next: Record<ProfileField, boolean>) => void;
  nameMissing: boolean;
}) {
  const { t } = useTranslation();
  const set = <K extends keyof ProfileDraft>(key: K, v: ProfileDraft[K]) => onChange({ ...value, [key]: v });
  const toggle = (field: ProfileField, label: string, children: ReactNode) => (
    <ReviewToggle label={label} checked={fields[field]} onChange={(on) => onFieldsChange({ ...fields, [field]: on })}>
      {children}
    </ReviewToggle>
  );
  const text = (field: "name" | "phone" | "email", label: string, error?: string) =>
    toggle(
      field,
      label,
      <Field label={label} htmlFor={`draft-${field}`} error={error}>
        <Input id={`draft-${field}`} value={value[field]} onChange={(e) => set(field, e.target.value)} />
      </Field>,
    );

  return (
    <Section title={t("profile.title")} description={t("onboarding.review.profileHint")}>
      <div className="flex flex-col gap-5">
        {text("name", t("profile.basic.name"), nameMissing ? t("onboarding.review.nameMissing") : undefined)}
        {text("phone", t("profile.basic.phone"))}
        {text("email", t("profile.basic.email"))}
        {toggle(
          "links",
          t("profile.links.title"),
          <ListEditor
            id="draft-links"
            title={t("profile.links.title")}
            rows={value.links}
            empty={{ label: "", url: "" }}
            onChange={(rows) => set("links", rows)}
            columns={[
              { key: "label", label: t("profile.links.label") },
              { key: "url", label: t("profile.links.url"), placeholder: "https://" },
            ]}
          />,
        )}
        {toggle(
          "education",
          t("profile.education.title"),
          <ListEditor
            id="draft-education"
            title={t("profile.education.title")}
            rows={value.education}
            empty={{ school: "", period: "", status: "", degree: "", note: "" }}
            onChange={(rows) => set("education", rows)}
            columns={[
              { key: "school", label: t("profile.education.school") },
              { key: "period", label: t("profile.education.period") },
              { key: "status", label: t("profile.education.status") },
              { key: "degree", label: t("profile.education.degree") },
              { key: "note", label: t("profile.education.note") },
            ]}
          />,
        )}
        {toggle(
          "skills",
          t("profile.skills.title"),
          <Field label={t("profile.skills.title")} hint={t("profile.skills.hint")} htmlFor="draft-skills">
            <Input id="draft-skills" value={value.skillsText} onChange={(e) => set("skillsText", e.target.value)} />
          </Field>,
        )}
        {toggle(
          "languages",
          t("profile.languages.title"),
          <ListEditor
            id="draft-languages"
            title={t("profile.languages.title")}
            rows={value.languages}
            empty={{ name: "", level: "" }}
            onChange={(rows) => set("languages", rows)}
            columns={[
              { key: "name", label: t("profile.languages.name") },
              { key: "level", label: t("profile.languages.level") },
            ]}
          />,
        )}
        {toggle(
          "additional",
          t("profile.additional.title"),
          <AdditionalInfoSection value={value.additional} onChange={(a) => set("additional", a)} />,
        )}
      </div>
    </Section>
  );
}
