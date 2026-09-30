# STATUS — 세션 인계 (새 세션은 이 파일부터)

> 60줄 이하로 유지한다. 완료된 태스크는 한 줄로 접고, 세부는 커밋/카드에 둔다.

## 현재
- 마일스톤: **M3 · 에이전트 런타임 · 채우기(fill) run** (M2 완료)
- 다음 태스크: **T3.8** AnthropicApiAgentRuntime → M3 완료 → 사용자와 실사용 점검 → M4 카드
- **병렬 실행 금지**(사용자 지시 2026-09-30, 메모리 부족) — 서브에이전트는 한 번에 하나.
- 차단 요소: 없음
- 결정: `.env.v2.bak` 보존(사용자 지시, 손대지 않음). `main` 은 M1 완료 시 push.

## 완료
- 2026-09-29 방향 전환 인터뷰 → `00-product.md`(D1~D15), `01-architecture.md`(§A1~§A10), `02-milestones.md` 작성.
  v2 는 `legacy/v2-telegram-recipe` 브랜치로 보존(origin push 완료).
- **M0 완료**(T0.1~T0.4): v2 코드·Temporal·Postgres·S3 제거, SQLite+Alembic(append-only 이력), `auto-apply` 진입점·JobRunner 뼈대·§A2 arch,
  v2 문서 삭제. 로컬 v2 데이터 삭제, `.env` 는 `.env.v2.bak` 백업 후 재생성. 세부는 각 커밋.
- **M1 완료**(T1.1~T1.5): 프로필·경험·답변KB·문서 SQLite, ProfileService·REST·로컬 보안(설치 토큰·Host/Origin), 이력서 초안 추출·v2 임포터,
  웹 콘솔(TanStack Router) 전 화면 + make check 웹 게이트. 주민번호 우회(전각·구분자)·multipart·XSS 결함은 자체 검증에서 수정.
- **M2 완료**(T2.1~T2.7): 전용 프로필 Chrome·클릭 분류기·테스트 짐 17종+·BrowserToolbox·SubmitGuard L2~L5·로그인 핸드오프·D17 단계 이동 자동 통과.
  짐 전 픽스처 FILL 제출 0건(D17 받아들인 위험 1종 제외). 남는 위험은 §A4·M3 끝 '실사용 점검'.
- T3.1 v3 상태기계(+INCIDENT)·ApplicationService.transition 유일 통로(AST 봉인 테스트)·조건부 UPDATE 동시 전이 방어·Alembic 0003.
  지적: 크래시 복구 SUBMITTING→FAILED 는 '제출됐을 수 있음'을 숨김 → T3.2 에서 INCIDENT 로. 16만 토큰·62회(교대 기준 근처).
- T3.2 JobRunner(jobs 0004·run_after 백오프·브라우저 슬롯 1)·재시도 분류(모르는 예외 FATAL, submit 은 절대 재시도 안 함)·크래시 복구
  (SUBMITTING→INCIDENT 반영). SQLite BEGIN IMMEDIATE 로 동시 claim 수정. job/run DTO 가 ports/ 에 있음 — T3.3 에서 contracts/ 로.
- T3.3 AgentRuntime port·Scripted·fill run(도구 결과만 상태에 영향, 한도 200회·1800초) — 한도로 중단 → 핸드오프 교대. 프롬프트 프로필 키가
  FillSource 패턴과 어긋나던 버그를 자체 검증에서 발견·수정. job/run DTO contracts/ 이동·errors.py 분리 완료.
- T3.4 ask_user·답변 KB·재진입 run — 민감 답은 에이전트에 값을 주지 않고(`source={user,key}` 로 앱이 채움) 프로세스 메모리에만.
  snapshot 으로 새던 구멍·재시작 뒤 빈 값 덮어쓰기를 자체 검증에서 수정. 범위 밖 수정(ai·config·bootstrap)은 카드 범위를 좁게 쓴 내 탓 — 정당.
- T3.5 MCP(streamable HTTP, `/mcp`) — run 토큰만(설치 토큰·만료·다른 run 거부, run 토큰으로 REST 거부), 보안 장치 6곳 끄기 실측. mcp SDK `<2` 고정.
- T3.6 ClaudeCliAgentRuntime — init 도구 목록이 우리 MCP 뿐임을 실행 중에도 강제, run 토큰은 env 로만, 프로세스 그룹째 정리.
  실제 claude+Chrome 짐 e2e AWAITING_APPROVAL·제출 0건. CLAUDE.md·auto-memory 끼어듦을 env 로 차단(6.5k→583 토큰).
- T3.7 bootstrap 패키지 분리·실제 포트를 금지 출처·MCP URL 에 주입·llm_provider 기본 claude_cli·run 한도 설정 키.
  ask_user 70초 대기가 실제 claude CLI MCP 에서 안 끊김 확인(MCP_TOOL_TIMEOUT 이 결정).

## 열린 질문 (다음 마일스톤 시작 전에 사용자에게)
- (백로그) 부하 시 `tests/api/test_profile_api.py::test_experience_crud[memory]` 1회 실패(재현 안 됨) — 플레이키 여부 조사.
- (백로그) `make check` 는 머신 부하에 민감(유휴 ~17초, 부하 시 60~75초) — 최대 원인 `tests/api/test_profile_api.py::test_upload_memory_peak_is_about_one_file[a.pdf]` 18초,
  콘솔 스크립트 스모크 2개 ~12초. 크기 축소 또는 `test-all` 로 이동 검토(다음 백엔드 카드에서).
- M4 전: fill run 에 비용 상한(`--max-budget-usd`)도 둘지 — 지금은 도구 수·시간 한도만(구독 CLI 기본이라 보류 중).
- M5: 직군 템플릿 디자인 톤(1안 여러 개 vs 직군당 1안)

## 세션 운영 프로토콜
1. 오케스트레이터(메인 세션)는 **이 파일 → 해당 마일스톤 카드 → 필요한 §A 절만** 읽는다. 소스 트리를 넓게 읽지 않는다.
2. 태스크 1개 = `spec-implementer` 서브에이전트 1회. 프롬프트는 **카드 ID 만** 넘긴다 (`"T0.1 을 수행하라"`).
3. 서브에이전트는 구현 + 테스트 + `make check` + 카드 단위 커밋까지 하고 **15줄 이하 보고**(커밋 해시, check 결과 요약, 범위 밖 발견사항)만 돌려준다.
4. 구현 에이전트가 커밋 전 **자체 검증**(수용 기준 실측·스펙 대조·절대 규칙 실측·과잉/미흡)까지 하고 보고한다. 별도 검증 에이전트는 띄우지 않는다(사용자 지시 2026-09-30).
5. 오케스트레이터는 보고를 보고 과잉·미흡을 지적한다. 고칠 게 있으면 구현 에이전트에 되돌리고, 수정은 **그 카드 커밋에 amend**(카드 1장 = 커밋 1개).
   잔재는 후속 카드로 이관, 이 파일·스펙 갱신도 같은 커밋에 얹는다. 그다음 카드로.
5a. **교대 규칙**(컨텍스트 오염 방지, 사용자 합의 2026-09-30): 구현 에이전트는 `.claude/handoffs/<카드>.md` 를 유지한다.
   에이전트가 중단(한도·네트워크)되거나 누적 토큰이 ~15만을 넘으면 SendMessage 로 이어 붙이지 말고 "핸드오프 작성 후 중단" →
   **새 spec-implementer** 에 "`<카드>` 를 핸드오프 노트부터 이어서 수행하라"로 교대. 카드는 에이전트 1회(~15만 토큰)에 끝날 크기로 자른다.
   에이전트는 토큰을 못 재므로 대리 지표 **도구 호출 ~60회**에서 스스로 PAUSED(T2.7: 115회·35만 토큰으로 교대 누락).
5b. **자동 재개**: 오케스트레이터 세션에 매시 :17 점검 cron(세션 전용, 7일 만료 — 새 세션이면 다시 등록)이 있다. 서브에이전트가
   한도로 실패하면 표시된 해제 시각 +3분에 일회성 재개 cron 도 건다. 재개는 항상 핸드오프 노트 + 새 에이전트로.
6. (현재 병렬 금지 — 위 "현재" 참조) 병렬이 허용될 때는 파일 범위가 겹치지 않는 카드만, HEAD 에서 만든 worktree 로.
7. 마일스톤 시작 시 상세 카드를 먼저 쓰고(오케스트레이터), 사용자 결정이 필요한 건 "열린 질문"을 먼저 푼다.
