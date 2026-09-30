import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

// 검토 항목 하나: 왼쪽 "채택" 체크(끄면 버림) + 오른쪽 내용. 버린 항목도 고칠 수는 있게 흐리게만 한다.
export function ReviewToggle({
  label,
  checked,
  onChange,
  children,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-start gap-3">
      <label className="flex shrink-0 items-center gap-1.5 pt-1 text-xs font-medium">
        <input
          type="checkbox"
          checked={checked}
          aria-label={`${label} ${t("onboarding.review.adopt")}`}
          onChange={(e) => onChange(e.target.checked)}
        />
        {checked ? t("onboarding.review.adopt") : t("onboarding.review.discard")}
      </label>
      <div className={cn("min-w-0 flex-1", !checked && "opacity-50")}>{children}</div>
    </div>
  );
}
