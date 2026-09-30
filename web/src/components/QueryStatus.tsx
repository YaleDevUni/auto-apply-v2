import type { UseQueryResult } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/error-message";

// 목록·양식 화면 공통의 "불러오는 중 / 실패 + 다시 시도". 성공이면 아무것도 그리지 않는다.
export function QueryStatus({ query }: { query: UseQueryResult<unknown> }) {
  const { t } = useTranslation();
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
  return null;
}
