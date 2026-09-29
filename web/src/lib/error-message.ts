import type { TFunction } from "i18next";
import { ApiError } from "@/lib/api";

const KNOWN = [
  "unique_identifier_rejected",
  "validation_error",
  "invalid_token",
  "origin_not_allowed",
  "host_not_allowed",
  "network",
] as const;
type KnownCode = (typeof KNOWN)[number];

function isKnown(code: string): code is KnownCode {
  return (KNOWN as readonly string[]).includes(code);
}

// 서버 메시지는 개발자용이라 화면에는 코드별 번역 문구를 쓴다. 검증 실패는 어느 칸인지 덧붙인다.
export function errorMessage(e: unknown, t: TFunction): string {
  if (!(e instanceof ApiError)) return t("errors.generic");
  const base = isKnown(e.code) ? t(`errors.${e.code}`) : t("errors.generic");
  const where = e.details
    .map((d) => d.loc.filter((part) => part !== "body").join("."))
    .filter(Boolean);
  return where.length > 0 ? `${base} (${where.join(", ")})` : base;
}
