import type { TFunction } from "i18next";
import { describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/error-message";

const t = i18n.t.bind(i18n) as TFunction;

describe("errorMessage", () => {
  it("주민번호 거부는 전용 문구이고 서버 메시지·입력값을 싣지 않는다", () => {
    const e = new ApiError(422, "unique_identifier_rejected", "server text", [{ loc: ["body"], msg: "x" }]);
    const msg = errorMessage(e, t);
    expect(msg).toContain("주민등록번호");
    expect(msg).not.toContain("server text");
  });

  it("검증 실패는 칸 위치를 덧붙인다 (body 접두는 뺀다)", () => {
    const e = new ApiError(422, "validation_error", "", [{ loc: ["body", "education", 0, "school"], msg: "" }]);
    expect(errorMessage(e, t)).toBe(`${t("errors.validation_error")} (education.0.school)`);
  });

  it("모르는 코드·ApiError 가 아닌 값은 일반 문구", () => {
    expect(errorMessage(new ApiError(500, "internal_error", ""), t)).toBe(t("errors.generic"));
    expect(errorMessage(new Error("boom"), t)).toBe(t("errors.generic"));
  });
});
