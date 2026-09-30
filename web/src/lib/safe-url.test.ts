import { describe, expect, it } from "vitest";
import { safeHttpUrl } from "@/lib/safe-url";

describe("safeHttpUrl", () => {
  it("http(s) 주소만 링크로 쓴다", () => {
    expect(safeHttpUrl("https://github.com/x")).toBe("https://github.com/x");
    expect(safeHttpUrl(" http://example.com ")).toBe("http://example.com/");
  });

  it("스크립트·데이터 스킴과 상대 경로·빈 값은 거부한다", () => {
    for (const raw of ["javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,<b>", "vbscript:x", "github.com/x", "", null, undefined]) {
      expect(safeHttpUrl(raw)).toBeNull();
    }
  });
});
