import { describe, expect, it } from "vitest";
import { formatBytes, formatDateTime } from "@/lib/format";

describe("format", () => {
  it("바이트는 B·KB·MB 로", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(10 * 1024 * 1024)).toBe("10.0 MB");
  });

  it("해석할 수 없는 시각은 원문 그대로", () => {
    expect(formatDateTime("nope", "ko")).toBe("nope");
    expect(formatDateTime("2026-09-30T01:02:03+00:00", "ko")).toContain("2026");
  });
});
