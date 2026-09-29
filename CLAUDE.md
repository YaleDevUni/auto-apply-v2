# auto-apply v3

누구나 자기 PC 에 설치해서 쓰는 구직 지원 도구. 사용자가 채용 링크를 던지면 **Claude 가 브라우저를
직접 조종**해 지원서를 채우고, **하네스가 최종 제출을 막고**, 사람이 웹 승인 큐에서 승인해야 제출된다.

> v2(텔레그램·공고수집·Recipe·wanted 전용·Temporal)는 `legacy/v2-telegram-recipe` 브랜치에 있다.
> main 은 v3 로 재작성 중이다 — **M0 가 끝나기 전까지 코드에는 v2 잔재가 남아 있다.**

## 작업 시작 전 (세션 관리)

1. **[docs/spec/STATUS.md](docs/spec/STATUS.md) 부터 읽는다** — 현재 마일스톤·다음 태스크·운영 프로토콜.
2. 필요한 것만 추가로: [00-product.md](docs/spec/00-product.md)(결정 D1~D15) ·
   [01-architecture.md](docs/spec/01-architecture.md)(§A1~§A10) · [02-milestones.md](docs/spec/02-milestones.md)(태스크 카드).
3. 태스크 구현은 `spec-implementer` 서브에이전트에 **카드 ID 만** 넘겨 위임하고, 메인 세션은 STATUS.md 를 갱신한다.
   컨텍스트를 아끼는 게 목적이다 — 메인 세션이 소스 트리를 넓게 읽지 않는다.

`docs/ARCHITECTURE.md`·`docs/architecture/` 는 **v2 문서**다(T0.4 에서 삭제). v3 설계 판단에 쓰지 않는다.

## 명령어 (T0.4 에서 v3 기준으로 갱신)

```bash
make check     # lint + type + arch + test  ← 커밋 전 필수
make test-fast # 빠른 반복용 (게이트 아님)
make web       # 웹 콘솔 dev 서버
```

## 절대 규칙 (00-product.md "절대 규칙"이 기준)

1. **에이전트는 최종 제출을 할 수 없다.** 제출은 사람 승인 뒤 하네스 코드가 한다 (§A4). 가이드·프롬프트·LLM 출력으로
   하네스를 풀 수 있는 통로를 만들지 않는다.
2. **`submit_mode=dry_run` 이 기본.** 사용자 확인 없이 `live` 로 바꾸지 않는다.
3. **CAPTCHA·SMS·본인인증 우회 금지, 계정 대리 생성 금지, 비밀번호 저장·타이핑 금지.** 로그인은 사람이 전용 크롬 창에서 한다.
4. **없는 사실을 쓰지 않는다.** 생성 문서·자소서 답변은 `ground_check` 통과 필수. LLM 출력은 Pydantic 검증 후에만 실행 계층에 닿는다.
5. **주민등록번호 등 고유식별정보는 저장하지 않는다.**
6. 지원 상태 전이는 `ApplicationService.transition()` 하나로만 (§A3).

## 계층 규칙 — `make arch` 가 강제 (§A2)

`domain`(순수) · `contracts`(DTO) · `ports`(Protocol) · `adapters`(구현) · `services`(유스케이스) · `runner` · `api` ·
`bootstrap`(★ 어댑터 생성 유일 지점).

새 외부 의존성: `ports/` Protocol(벤더 타입 노출 금지) → 예외 계약(`domain/errors.py`) → **구현 2개**(실제 + 테스트 대역)
→ `tests/ports/` contract test params 추가 → `bootstrap.py`·`config.py` 선택지 추가.

## 개발 프로세스

**테스트 없는 구현은 완료가 아니다.**

- 구현과 같은 커밋에 테스트. `make check` 를 **실제로 실행해** 통과를 확인하고, 보고는 실제 출력 근거로.
- 새 port → contract test, 하네스 변경 → 테스트 짐(§A4) 전 픽스처 통과, LLM 스키마 변경 → 회귀 테스트.
- 테스트 마커는 "무엇이 있어야 도는가"로만: `native`(Chrome·claude CLI 등 로컬 바이너리 필요). 아무것도 안 붙이면 기본 실행.
- **mac·Windows 둘 다** 돈다: 경로는 `pathlib`, 데이터 경로는 `platformdirs`, 셸 스크립트 대신 Python.

**커밋 규칙**

- 큰 기능 단위(= 태스크 카드 단위)로 커밋. 여러 기능을 한 커밋에 몰지도, 파일마다 쪼개지도 않는다.
- 대화 턴이 끝나면 묻지 않고 커밋한다. 커밋 전 `make check` 통과가 전제.
- 설계가 바뀌면 `docs/spec/` 해당 절을 같은 커밋에서 수정.
- **커밋에 `Co-Authored-By: Claude` 트레일러를 넣지 않는다.**

## 코드 컨벤션

- 절대 import 만 (`from auto_apply.x import y`).
- DTO 는 `extra="forbid"` + `frozen=True`. 열거형은 `StrEnum`.
- Protocol 속성은 `@property` 로 선언.
- 로그는 structlog, **모든 로그에 `application_id`·`run_id` 구조화 필드.**
- 주석은 "왜"만. 설계 근거는 `§A…`·`D…` 로 참조.
- 파일이 ~200줄을 넘으면 책임을 쪼갤 지점을 찾는다.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
