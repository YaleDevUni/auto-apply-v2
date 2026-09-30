# STATUS — 세션 인계 (새 세션은 이 파일부터)

> 60줄 이하로 유지한다. 완료된 태스크는 한 줄로 접고, 세부는 커밋/카드에 둔다.

## 현재
- 마일스톤: **M2 · 브라우저 호스트 · 제출 차단 하네스** (카드 T2.1~T2.6 작성됨)
- 다음 태스크: **T2.1 재개**(worktree t2.1 부분 작업 있음) → T2.3 재개(worktree t2.1 부분 작업 있음) → T2.3 재개(worktree t2.3 부분 작업 있음) → T2.4…
- **병렬 실행 금지**(사용자 지시 2026-09-30, 메모리 부족) — 서브에이전트는 한 번에 하나.
- 차단 요소: 없음
- 결정: `.env.v2.bak` 보존(사용자 지시, 손대지 않음). `main` 은 M1 완료 시 push.

## 완료
- 2026-09-29 방향 전환 인터뷰 → `00-product.md`(D1~D15), `01-architecture.md`(§A1~§A10), `02-milestones.md` 작성.
  v2 는 `legacy/v2-telegram-recipe` 브랜치로 보존(origin push 완료).
- T0.1 폐기 코드 삭제 — 검증 PASS_WITH_NOTES. 카드 밖 삭제(ApplicationWorkflow·API 라우터·cli·포트폴리오 매핑)는 정당,
  잔재는 T0.2·M1·M6 카드로 이관. 웹 콘솔은 M1 전까지 동작 안 함(엔드포인트 삭제됨). D16(TanStack Router) 추가.
- T0.2 Temporal·Postgres·S3 제거, SQLite+Alembic — 검증 PASS_WITH_NOTES. 상태 이력 A→B→A 버그를 append-only 로 고쳐 amend.
  잔재는 T0.3·M3·M4·M5·M7 카드로 이관. 로컬 v2 데이터(`var/` 전체·config v2 파일) 삭제, `.env` 는 `.env.v2.bak` 백업 후 v3 로 재생성.
- T0.3 `auto-apply` 진입점·JobRunner 뼈대·§A2 arch — 검증 PASS_WITH_NOTES. cwd `.env` 가 dry_run 을 덮는 통로(개발 모드만 허용)와
  마이그레이션 부분 적용(pysqlite 트랜잭션 레시피)을 고쳐 amend. 잔재는 M1·M4·M7 로 이관.
- T0.4 v2 문서 삭제·README v3·링크 검사 테스트 — 검증 PASS_WITH_NOTES, **M0 마일스톤 검증 완료**(카드 간 회귀 없음).
- T1.1 프로필·경험·답변KB·문서 SQLite 전환 — 검증 PASS_WITH_NOTES. 주민번호 미탐(전각·구분자·zero-width)과
  model_copy 우회를 NFKC 정규화 + 저장 시점 재검사로 막아 amend. user_id 컬럼은 port 호환상 유지.
- T1.2 ProfileService·REST API·로컬 보안 — 검증 PASS_WITH_NOTES. 직접 만든 multipart 파서 결함 5개 → python-multipart 교체,
  BlobStore.delete, 개발 전용 CORS·/docs 를 개발 모드로 한정해 amend. 토큰 강화는 M7 이관. 이후 검증은 구현 에이전트 자체 검증으로 전환.
- T1.4 웹 콘솔 — TanStack Router·레이아웃·인적사항 화면·vitest. 브라우저 실기동 자체 검증. 웹 게이트 make check 편입은 T1.5 이관.
- T1.3 이력서 초안 추출·v2 yaml 임포터 — 보강 라운드: 주민번호 LLM 전 가림, 초안 목록 API, 업로드 본문 주민번호 거부,
  온보딩 전용 업로드 경로(문서 비저장). 동기 LLM 추출의 JobRunner 이전은 M3.
- T1.5 웹 경험·답변·문서·온보딩 + make check 웹 게이트 — 자체 검증에서 경험 링크 `javascript:` XSS 발견·수정. **M1 완료.**
- T2.2 제출 클릭 분류기(허용 목록·애매하면 Risky)·완료 어휘. type=submit "다음" 다단계 문제는 T2.5 설계 과제로 이관.
- T2.1 BrowserHost — 설치 Chrome 탐지·전용 프로필 잠금·기본 프로필 거부(D6)·지연 기동. Windows 잠금 분기 실측은 M7.

## 열린 질문 (다음 마일스톤 시작 전에 사용자에게)
- (백로그) `make check` 단독 ~22초(94초는 병렬 메모리 압박 탓) — 최대 원인 `tests/api/test_profile_api.py::test_upload_memory_peak_is_about_one_file[a.pdf]` 18초,
  콘솔 스크립트 스모크 2개 ~12초. 크기 축소 또는 `test-all` 로 이동 검토(다음 백엔드 카드에서).
- M5: 직군 템플릿 디자인 톤(1안 여러 개 vs 직군당 1안)

## 세션 운영 프로토콜
1. 오케스트레이터(메인 세션)는 **이 파일 → 해당 마일스톤 카드 → 필요한 §A 절만** 읽는다. 소스 트리를 넓게 읽지 않는다.
2. 태스크 1개 = `spec-implementer` 서브에이전트 1회. 프롬프트는 **카드 ID 만** 넘긴다 (`"T0.1 을 수행하라"`).
3. 서브에이전트는 구현 + 테스트 + `make check` + 카드 단위 커밋까지 하고 **15줄 이하 보고**(커밋 해시, check 결과 요약, 범위 밖 발견사항)만 돌려준다.
4. 구현 에이전트가 커밋 전 **자체 검증**(수용 기준 실측·스펙 대조·절대 규칙 실측·과잉/미흡)까지 하고 보고한다. 별도 검증 에이전트는 띄우지 않는다(사용자 지시 2026-09-30).
5. 오케스트레이터는 보고를 보고 과잉·미흡을 지적한다. 고칠 게 있으면 구현 에이전트에 되돌리고, 수정은 **그 카드 커밋에 amend**(카드 1장 = 커밋 1개).
   잔재는 후속 카드로 이관, 이 파일·스펙 갱신도 같은 커밋에 얹는다. 그다음 카드로.
6. (현재 병렬 금지 — 위 "현재" 참조) 병렬이 허용될 때는 파일 범위가 겹치지 않는 카드만, HEAD 에서 만든 worktree 로.
7. 마일스톤 시작 시 상세 카드를 먼저 쓰고(오케스트레이터), 사용자 결정이 필요한 건 "열린 질문"을 먼저 푼다.
