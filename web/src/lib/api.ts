// 백엔드 호출의 단일 통로. 변경 요청에는 `/api/session` 에서 받은 설치별 토큰을 붙인다 (§A10).

// 개발(vite dev)은 `make api`(8000)를 CORS 로 부르고(서버가 127.0.0.1 에만 바인드해 localhost→::1 해석을 피한다), 빌드본은 API 서버가 같은 출처로 서빙한다(M7).
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export interface ApiErrorDetail {
  loc: (string | number)[];
  msg: string;
}

// 서버 에러는 한 모양이다: {"error": {"code", "message", "details"}} (§A10).
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorDetail[];

  constructor(status: number, code: string, message: string, details: ApiErrorDetail[] = []) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

// 서버에 닿지 못한 경우(꺼져 있음 등). status 0 으로 구분한다.
const NETWORK_ERROR = "network";

interface SessionInfo {
  token: string;
  header: string;
}

let session: Promise<SessionInfo> | null = null;

function getSession(): Promise<SessionInfo> {
  session ??= send("/api/session", { cache: "no-store" }).then(
    (res) => res.json() as Promise<SessionInfo>,
  );
  // 실패한 약속을 캐시에 남기면 서버가 뜬 뒤에도 영영 실패한다.
  session.catch(() => {
    session = null;
  });
  return session;
}

async function send(path: string, init: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, init);
  } catch {
    throw new ApiError(0, NETWORK_ERROR, "network error");
  }
  if (!res.ok) throw await toApiError(res);
  return res;
}

async function toApiError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as { error?: { code: string; message: string; details?: ApiErrorDetail[] } };
    if (body.error) {
      return new ApiError(res.status, body.error.code, body.error.message, body.error.details ?? []);
    }
  } catch {
    // JSON 이 아닌 응답(프록시 오류 페이지 등)은 아래 일반 에러로.
  }
  return new ApiError(res.status, "http_error", res.statusText);
}

async function withToken(init: RequestInit): Promise<RequestInit> {
  if (!MUTATING.has((init.method ?? "GET").toUpperCase())) return init;
  const { token, header } = await getSession();
  return { ...init, headers: { ...init.headers, [header]: token } };
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // FormData 는 브라우저가 boundary 를 넣은 Content-Type 을 직접 붙여야 한다 — 덮어쓰면 서버가 본문을 못 읽는다.
  const json = !(init.body instanceof FormData);
  const base: RequestInit = json
    ? { ...init, headers: { "Content-Type": "application/json", ...init.headers } }
    : init;
  let res: Response;
  try {
    res = await send(path, await withToken(base));
  } catch (e) {
    // 서버가 다른 데이터 디렉터리로 재기동하면 토큰이 바뀐다 — 한 번만 새로 받아 다시 보낸다.
    if (!(e instanceof ApiError && e.code === "invalid_token")) throw e;
    session = null;
    res = await send(path, await withToken(base));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function isNotFound(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

// multipart `file` 필드 하나로 올린다 — 서버 `api/multipart.py` 가 받는 모양.
export function uploadFile<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return request<T>(path, { method: "POST", body: form });
}
