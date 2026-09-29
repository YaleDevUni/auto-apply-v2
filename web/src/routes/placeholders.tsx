import { useTranslation } from "react-i18next";

// 경험·답변·문서 화면은 T1.5 에서 채운다 — 내비게이션 구조만 먼저 잡는다.
export function ComingSoon({ title }: { title: "experiences" | "answers" | "documents" }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-2">
      <h1 className="text-xl font-semibold">{t(`nav.${title}`)}</h1>
      <p className="text-muted-foreground text-sm">{t("common.comingSoon")}</p>
    </div>
  );
}

export function NotFound() {
  const { t } = useTranslation();
  return <p className="text-muted-foreground text-sm">{t("common.notFound")}</p>;
}
