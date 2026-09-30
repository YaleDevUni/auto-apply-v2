// 사용자·LLM 추출(이력서 원문)에서 온 주소를 링크로 그릴 때 — `javascript:` 등은 클릭하면 콘솔 출처에서 실행된다.
// http(s) 만 링크로 쓰고 나머지는 글자로만 보여 준다.
export function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw.trim());
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}
