# FastAPI 표면

> `docs/ARCHITECTURE.md` 색인의 §7. 절 번호는 코드 주석이 참조하므로 바뀌지 않는다.

---

## 7. FastAPI 표면

현재 구현된 라우트 전체다 (`api/routers/`, `api/main.py`):

```
POST   /applications                  → ApplicationWorkflow 시작 (즉시 202 + workflow_id)
GET    /applications/{id}             DB 사실 + Temporal query 병합
POST   /applications/{id}/approve     → signal                        202
POST   /applications/{id}/reject      → signal                        202
POST   /applications/{id}/revise      → signal (텔레그램 없이도 REVISE 트리거)  202
POST   /applications/{id}/schedule    → signal                        202
POST   /applications/{id}/cancel      → signal                        202
GET    /recipes/{platform}            버전 목록 (없으면 빈 리스트)
POST   /recipes/{platform}/promote    candidate → active (사람만). PolicyViolation → 409
POST   /telegram/webhook              Bot 진입점 — 항상 200 (아래)
GET    /healthz
```

규칙: **API 핸들러 안에서 LLM/Playwright를 실행하지 않는다.** 워크플로우를 시작하고 즉시 응답한다.

**`/telegram/webhook`은 처리에 실패해도 200을 돌려준다.** 텔레그램 서버는 비-200 응답을 "전달
실패"로 보고 같은 업데이트를 재전송하는데, 그 재시도가 우리 쪽에서도 똑같이 실패하면 실패 알림이
매 재전송마다 나가 알림 폭풍이 된다(실측 픽스, `6a0c94e`). 사람에게는 별도로
`telegram/bridge.inbound_failure_message`로 한 번만 알린다(§11.2d 4번) — 텔레그램의 재시도
메커니즘을 우리 알림 채널의 재시도로 겸용하지 않는다.

`api/main.py`의 미처리 예외 처리기는 같은 이유로 존재한다 — 웹훅 경로 밖에서 500이 나면
텔레그램 서버만 알고 사람은 모르므로, `kind=API_ERROR`로 `Notifier`에 흘린다.

**의도적으로 없는 것**
- `POST /jobs` — 공고 수집은 REST가 아니라 Temporal Schedule(§11.2b) / CLI `collect` /
  텔레그램 `collect_now` 도구로 트리거한다. HTTP 진입점을 하나 더 두면 rate limit 근거
  (`platform_policies`)를 우회할 경로가 늘어난다.
- `GET /metrics` — §9.4대로 관측성 백엔드를 아직 붙이지 않았다. Temporal UI가 1차 운영 콘솔이다.
