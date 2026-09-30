import type { TFunction } from "i18next";
import { ApiError } from "@/lib/api";

// §A10 의 에러 코드 중 화면 문구가 있는 것.
const KNOWN = [
  "unique_identifier_rejected",
  "validation_error",
  "not_found",
  "conflict",
  "too_large",
  "unsupported_type",
  "empty",
  "extraction_failed",
  "llm_invalid_output",
  "llm_auth_required",
  "llm_quota_exceeded",
  "llm_error",
  "invalid_token",
  "origin_not_allowed",
  "host_not_allowed",
  "network",
] as const;
type KnownCode = (typeof KNOWN)[number];

function isKnown(code: string): code is KnownCode {
  return (KNOWN as readonly string[]).includes(code);
}

// 같은 코드라도 화면마다 뜻이 다를 때(문서 본문의 주민번호 등) 호출한 쪽 문구로 바꾼다.
export type ErrorOverrides = Partial<Record<KnownCode, string>>;

// 서버 메시지는 개발자용이라 화면에는 코드별 번역 문구를 쓴다. 검증 실패는 어느 칸인지 덧붙인다.
export function errorMessage(e: unknown, t: TFunction, overrides: ErrorOverrides = {}): string {
  if (!(e instanceof ApiError)) return t("errors.generic");
  const base = isKnown(e.code) ? (overrides[e.code] ?? t(`errors.${e.code}`)) : t("errors.generic");
  const where = e.details
    .map((d) => d.loc.filter((part) => part !== "body").join("."))
    .filter(Boolean);
  return where.length > 0 ? `${base} (${where.join(", ")})` : base;
}
