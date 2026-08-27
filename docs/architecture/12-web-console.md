# Web Console

> `docs/ARCHITECTURE.md` 색인의 §12. 절 번호는 코드 주석이 참조하므로 바뀌지 않는다.

---

## 12. Web Console

텔레그램(§6)과 같은 역할 — 사람이 지원을 시작하고 승인/거절/수정요청/취소하는 콘솔 하나를
더 둔다. `web/`(신규, Vite + React + TypeScript + shadcn/ui)가 프론트엔드, 백엔드는 기존
FastAPI(§7)에 라우터 하나(`api/routers/web.py`)만 추가했다 — 새 서비스가 아니다.

**채팅 에이전트를 쓰지 않는다.** 텔레그램의 ReAct 루프(`telegram/agent.py`, §6)는 인터페이스가
자유 텍스트뿐이라 의도 파악(LLM 도구 선택)이 필요해서 있는 것이다. 웹은 입력칸/버튼이 이미
구조화된 의도다 — URL 입력칸이 곧 `apply_by_url` 호출이고, 승인 버튼이 곧 `ApproveSignal`이다.
LLM을 하나 더 끼우면 텔레그램이 실제로 겪은 "같은 도구를 의도치 않게 여러 번 호출"(§6,
2026-08-21 사고) 같은 실패 모드만 새로 생기고 얻는 게 없다 — REST를 직접 호출한다.

**인증이 없다.** 이 콘솔은 실제 제출 승인 권한을 갖는 화면이라 텔레그램의 chat_id allowlist에
해당하는 보호가 필요한데, **사용자가 명시적으로 "인증 없음, 로컬/사설망 전용"을 선택했다**
(2026-08-27). 외부에 노출하지 않는 것이 전제 — CORS 도 와일드카드가 아니라
`config.py`의 `web_cors_origin`(기본 `http://localhost:5173`, Vite dev 서버) 하나만 연다.

**새 엔드포인트 3개** (`api/routers/web.py`, `/applications` prefix — 기존 `applications.py`
(§7)를 그대로 재사용하고 이것들만 추가):

```
GET  /applications                    목록. list_recent + job 캐시 join(회사/직무/링크,
                                       best-effort) — telegram/_agent_tools.py::_list_applications
                                       와 같은 패턴. "진행 중"/"히스토리" 구분은 프론트가 state로 한다.
POST /applications/apply-by-url       apply_intake.apply_by_url 를 그대로 호출하는 얇은 래퍼.
GET  /applications/{id}/pending       ApplicationWorkflow.pending_decision query + PDF 서명
                                       URL(BlobStore.presign). 텔레그램 승인 메시지(DecisionRequest)
                                       와 같은 정보(제목/공고 링크/모드 배지/주의사항 3종)를 웹
                                       승인 카드에 그대로 보여준다.
```

기존 `POST /applications`/`GET /applications/{id}`/`approve`/`reject`/`revise`/`cancel`은
그대로 쓴다 — `approve` 등은 nonce 없이 빈 body(`{}`)로 불러도 워크플로우의 `_nonce_ok`가
"신뢰된 직접 호출"로 통과시킨다(텔레그램 없이도 쓸 수 있는 경로, 원래 `/revise`가 이미 그렇게
문서화돼 있었다). `apply_intake.ApplyByUrlResult`에 `application_id` 필드를 추가했다(기존
`label`만으로는 프론트가 지원 시작 직후 상세 화면으로 이동할 id가 없었다) — 기본값 있는 추가라
텔레그램 도구/기존 테스트는 영향 없다.

**PDF 이력서 캐시가 사라지면 뷰어도 사라진다.** `ResumeRepository`는 application_id 당 최신
1건만 들고 있고(§2.3), COMPLETED/CANCELLED 로 끝나면 지운다(§ resume-cache-and-expired-
deprioritize). `GET .../pending`은 `pending_decision` query가 `has_pending=True`일 때만
`request.artifact_url`을 채워주므로(§6 `PendingDecisionView`), 승인 대기 중이 아니면
`resume_url`이 없다 — 텔레그램과 동일한 한계다.

**실시간 진행상황은 폴링이다.** 이 프로젝트엔 push 인프라(SSE/WebSocket)가 없다(§9.4 관측성도
"미완"으로 남아있다) — TanStack Query의 `refetchInterval`로 상세 화면 열려 있을 때 `GET
/applications/{id}`(state/history) + `GET .../pending`을 짧은 주기로, 목록은 더 느슨한
주기로 다시 부른다. 상태관리는 TanStack Query만 쓴다 — 화면이 몇 개 안 돼 전역 스토어
(Zustand 등)가 딱히 필요하지 않았다.
