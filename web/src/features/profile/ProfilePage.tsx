import { useBlocker } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Field, Section } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AdditionalInfoSection } from "@/features/profile/AdditionalInfoSection";
import { ListEditor } from "@/features/profile/ListEditor";
import { toBody, toDraft, type ProfileDraft } from "@/features/profile/profile-form";
import { errorMessage } from "@/lib/error-message";
import type { Profile } from "@/lib/profile-api";
import { useProfile, useSaveProfile } from "@/lib/queries";

export function ProfilePage() {
  const { t } = useTranslation();
  const query = useProfile();

  if (query.isPending) return <p className="text-muted-foreground text-sm">{t("common.loading")}</p>;
  if (query.isError) {
    return (
      <div className="flex flex-col items-start gap-2">
        <p className="text-destructive text-sm">{errorMessage(query.error, t)}</p>
        <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
          {t("common.retry")}
        </Button>
      </div>
    );
  }
  return <ProfileForm initial={query.data} />;
}

function ProfileForm({ initial }: { initial: Profile | null }) {
  const { t } = useTranslation();
  const save = useSaveProfile();
  const [baseline, setBaseline] = useState(() => toDraft(initial));
  const [draft, setDraft] = useState(baseline);
  const [nameError, setNameError] = useState(false);

  const dirty = JSON.stringify(toBody(draft)) !== JSON.stringify(toBody(baseline));
  useBlocker({
    shouldBlockFn: () => dirty && !window.confirm(t("common.unsavedLeave")),
    enableBeforeUnload: () => dirty,
  });

  const set = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body = toBody(draft);
    if (body.name === "") {
      setNameError(true);
      return;
    }
    save.mutate(body, {
      onSuccess: (saved) => {
        const next = toDraft(saved);
        setBaseline(next);
        setDraft(next);
        toast.success(t("common.saved"));
      },
      onError: (err) => toast.error(errorMessage(err, t)),
    });
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-8" noValidate>
      {/* 양식이 길어 저장 버튼을 스크롤해도 보이게 둔다. */}
      <header className="bg-background sticky top-0 z-10 flex items-start justify-between gap-4 py-2">
        <div>
          <h1 className="text-xl font-semibold">{t("profile.title")}</h1>
          <p className="text-muted-foreground text-sm">{t("profile.description")}</p>
        </div>
        <Button type="submit" disabled={!dirty || save.isPending}>
          {save.isPending ? t("common.saving") : t("common.save")}
        </Button>
      </header>

      <Section title={t("profile.basic.title")}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field
            label={t("profile.basic.name")}
            htmlFor="profile-name"
            error={nameError ? t("profile.basic.nameRequired") : undefined}
          >
            <Input
              id="profile-name"
              value={draft.name}
              required
              aria-invalid={nameError || undefined}
              onChange={(e) => {
                setNameError(false);
                set("name", e.target.value);
              }}
            />
          </Field>
          <Field label={t("profile.basic.phone")} htmlFor="profile-phone">
            <Input id="profile-phone" type="tel" value={draft.phone} onChange={(e) => set("phone", e.target.value)} />
          </Field>
          <Field label={t("profile.basic.email")} htmlFor="profile-email">
            <Input id="profile-email" type="email" value={draft.email} onChange={(e) => set("email", e.target.value)} />
          </Field>
        </div>
      </Section>

      <ListEditor
        id="links"
        title={t("profile.links.title")}
        rows={draft.links}
        empty={{ label: "", url: "" }}
        onChange={(rows) => set("links", rows)}
        columns={[
          { key: "label", label: t("profile.links.label"), placeholder: t("profile.links.labelPlaceholder") },
          { key: "url", label: t("profile.links.url"), placeholder: "https://" },
        ]}
      />

      <ListEditor
        id="education"
        title={t("profile.education.title")}
        rows={draft.education}
        empty={{ school: "", period: "", status: "", degree: "", note: "" }}
        onChange={(rows) => set("education", rows)}
        columns={[
          { key: "school", label: t("profile.education.school") },
          { key: "period", label: t("profile.education.period"), placeholder: t("profile.education.periodPlaceholder") },
          { key: "status", label: t("profile.education.status"), placeholder: t("profile.education.statusPlaceholder") },
          { key: "degree", label: t("profile.education.degree") },
          { key: "note", label: t("profile.education.note") },
        ]}
      />

      <Section title={t("profile.skills.title")}>
        <Field label={t("profile.skills.label")} hint={t("profile.skills.hint")} htmlFor="profile-skills">
          <Input id="profile-skills" value={draft.skillsText} onChange={(e) => set("skillsText", e.target.value)} />
        </Field>
      </Section>

      <ListEditor
        id="languages"
        title={t("profile.languages.title")}
        rows={draft.languages}
        empty={{ name: "", level: "" }}
        onChange={(rows) => set("languages", rows)}
        columns={[
          { key: "name", label: t("profile.languages.name") },
          { key: "level", label: t("profile.languages.level"), placeholder: t("profile.languages.levelPlaceholder") },
        ]}
      />

      <AdditionalInfoSection value={draft.additional} onChange={(a) => set("additional", a)} />
    </form>
  );
}
