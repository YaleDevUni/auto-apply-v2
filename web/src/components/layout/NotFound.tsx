import { useTranslation } from "react-i18next";

export function NotFound() {
  const { t } = useTranslation();
  return <p className="text-muted-foreground text-sm">{t("common.notFound")}</p>;
}
