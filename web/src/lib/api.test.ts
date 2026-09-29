import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const HEADER = "X-Auto-Apply-Token";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function errorBody(code: string) {
  return { error: { code, message: code, details: [] } };
}

// 모듈 수준 세션 캐시를 테스트마다 비운다.
async function loadApi() {
  vi.resetModules();
  return import("@/lib/api");
}

describe("api request", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const headersOf = (call: number) => (fetchMock.mock.calls[call][1] as RequestInit).headers as Record<string, string>;

  it("GET 에는 토큰을 붙이지 않고 세션도 받지 않는다", async () => {
    const { request } = await loadApi();
    fetchMock.mockResolvedValueOnce(json(200, { ok: true }));
    await request("/api/profile");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(headersOf(0)[HEADER]).toBeUndefined();
  });

  it("변경 요청에는 /api/session 에서 받은 토큰을 붙이고, 토큰은 캐시한다", async () => {
    const { request } = await loadApi();
    fetchMock
      .mockResolvedValueOnce(json(200, { token: "tok-1", header: HEADER }))
      .mockResolvedValueOnce(json(200, {}))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    await request("/api/profile", { method: "PUT", body: "{}" });
    await expect(request("/api/answers/a", { method: "DELETE" })).resolves.toBeUndefined();
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).toEqual([
      "http://127.0.0.1:8000/api/session",
      "http://127.0.0.1:8000/api/profile",
      "http://127.0.0.1:8000/api/answers/a",
    ]);
    expect(headersOf(1)[HEADER]).toBe("tok-1");
    expect(headersOf(2)[HEADER]).toBe("tok-1");
  });

  it("invalid_token 이면 토큰을 한 번만 다시 받아 재시도한다", async () => {
    const { request } = await loadApi();
    fetchMock
      .mockResolvedValueOnce(json(200, { token: "old", header: HEADER }))
      .mockResolvedValueOnce(json(403, errorBody("invalid_token")))
      .mockResolvedValueOnce(json(200, { token: "new", header: HEADER }))
      .mockResolvedValueOnce(json(200, { saved: true }));
    await expect(request("/api/profile", { method: "PUT" })).resolves.toEqual({ saved: true });
    expect(headersOf(3)[HEADER]).toBe("new");
  });

  it("다른 403(origin_not_allowed)은 재시도하지 않고 코드를 그대로 올린다", async () => {
    const { request, ApiError } = await loadApi();
    fetchMock
      .mockResolvedValueOnce(json(200, { token: "t", header: HEADER }))
      .mockResolvedValueOnce(json(403, errorBody("origin_not_allowed")));
    const err = await request("/api/profile", { method: "PUT" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as InstanceType<typeof ApiError>).code).toBe("origin_not_allowed");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("세션 요청이 실패해도 캐시에 남지 않아 다음 요청은 다시 받는다", async () => {
    const { request } = await loadApi();
    fetchMock
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(json(200, { token: "t", header: HEADER }))
      .mockResolvedValueOnce(json(200, {}));
    await expect(request("/api/profile", { method: "PUT" })).rejects.toMatchObject({ code: "network", status: 0 });
    await request("/api/profile", { method: "PUT" });
    expect(headersOf(2)[HEADER]).toBe("t");
  });

  it("에러 스키마가 아닌 응답도 ApiError 로 바꾼다", async () => {
    const { request } = await loadApi();
    fetchMock.mockResolvedValueOnce(new Response("<html>", { status: 502, statusText: "Bad Gateway" }));
    await expect(request("/api/profile")).rejects.toMatchObject({ status: 502, code: "http_error" });
  });
});

describe("profileApi.get", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("404 는 null(아직 저장 안 함)이고 다른 에러는 올린다", async () => {
    vi.resetModules();
    const { profileApi } = await import("@/lib/profile-api");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(404, errorBody("not_found")))
      .mockResolvedValueOnce(json(500, errorBody("internal_error")));
    vi.stubGlobal("fetch", fetchMock);
    await expect(profileApi.get()).resolves.toBeNull();
    await expect(profileApi.get()).rejects.toMatchObject({ status: 500 });
  });
});
