# auto-apply 웹 콘솔

React + Vite SPA. 라우팅 TanStack Router, 서버 상태 TanStack Query (D16). 문구는 `src/i18n/ko.ts` 키로만 쓴다 (D14).

```bash
npm ci
npm run dev    # http://localhost:5173 — API 는 127.0.0.1:8000 (`make api`), VITE_API_BASE_URL 로 바꿀 수 있다
npm run build
npm run lint
```

- `src/lib/api.ts` — 유일한 fetch 통로. 변경 요청에 `/api/session` 토큰 헤더를 붙인다 (§A10).
- `src/router.tsx` — 라우트 트리. `src/components/layout/` — 공통 레이아웃.
- `src/features/<화면>/` — 화면별 컴포넌트와 양식 변환.

수동 확인 항목은 `docs/spec/checklists/m1-web.md`.
