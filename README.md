# auto-apply v3

> **채용 링크를 주면 Claude 가 브라우저로 지원서를 끝까지 채워 두고, 사람이 승인해야만 제출되는 로컬 설치형 구직 지원 도구입니다.**
>
> 직군과 상관없이 누구나 자기 PC(mac·Windows)에 설치해 씁니다. 에이전트는 최종 제출 버튼을 누를 수 없습니다 —
> 제출은 사람이 웹 승인 큐에서 승인한 뒤에만, 하네스 코드가 기록된 값과 대조하고 나서 수행합니다.

> **현재 상태: v3 로 재작성 중입니다 (M0 · 정리와 뼈대).** 아래 기능 설명은 목표 설계이며, 진행 상황은
> [`docs/spec/STATUS.md`](docs/spec/STATUS.md) 에서 확인하실 수 있습니다.
> 이전 버전(v2: 텔레그램·공고 수집·Recipe·Temporal)은 `legacy/v2-telegram-recipe` 브랜치에 있습니다.

---

## 사용 흐름

```
 ① 온보딩     웹에서 인적사항·학력·경력·경험 입력 (기존 이력서 PDF/DOCX 로 초안 자동 추출)
      │
 ② 트리거     채용 링크를 올립니다 (공고 URL, 또는 회사 채용 페이지 + 자연어)
      │
 ③ 이동·로그인 에이전트가 공고까지 이동 ── 로그인 벽 ──▶ 사람이 전용 크롬 창에서 직접 로그인 → "계속"
      │
 ④ 채우기     폼 작성·파일 업로드·자소서 답변 초안 ── 프로필에 없는 필수 항목 ──▶ 사람에게 질문
      │
 ⑤ 제출 차단  하네스가 최종 제출을 코드 레벨에서 막습니다. 에이전트는 "검토 준비됨"까지만 갑니다
      │
 ⑥ 승인 큐    스크린샷·입력값·생성 문서를 보고 승인 / 수정요청 / 거절
      │
 ⑦ 제출       재진입 → 기록된 값으로 다시 채움 → 값 대조 → 하네스가 제출 (기본은 dry_run)
      │
 ⑧ 학습       실수·수정요청·사람 개입에서 교훈을 뽑아 사이트별·전역 가이드에 자동 누적
```

---

## 구성

```
┌──────────── 사용자 PC (mac / Windows) ─────────────────────────────────────┐
│                                                                            │
│  auto-apply (단일 Python 프로세스, uv)                                       │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────────────────────┐  │
│  │ FastAPI      │   │ JobRunner     │   │ BrowserHost                  │  │
│  │  REST + 정적  │──▶│ (asyncio,     │──▶│  Playwright ─ CDP ─▶ Chrome  │  │
│  │  웹(React)   │   │  DB 큐 소비)   │   │  전용 프로필 (headful)        │  │
│  │  MCP(HTTP)   │   └──────┬────────┘   │  + SubmitGuard (제출 차단)    │  │
│  └──────┬───────┘          │            └──────────────▲───────────────┘  │
│         │                  ▼                           │ BrowserToolbox    │
│         │           ┌─────────────┐   tool calls       │                   │
│         │           │AgentRuntime │────────────────────┘                   │
│         │           │ CLI | API   │── claude -p (구독) / Anthropic API       │
│         │           └─────────────┘                                        │
│         ▼                                                                  │
│   SQLite + 파일 저장소 (platformdirs 데이터 디렉터리)                          │
└────────────────────────────────────────────────────────────────────────────┘
```

- **프로세스는 하나입니다.** 브라우저를 소유한 프로세스가 제출 차단 하네스도 소유해야 우회 경로가 생기지 않습니다.
- **외부 인프라가 없습니다.** Docker·Temporal·Postgres 없이 SQLite 와 로컬 파일만 씁니다.
- **LLM 경로는 두 가지입니다.** 기본은 로컬 Claude Code CLI(구독), 선택으로 Anthropic API 키.
- 서버는 `127.0.0.1` 에만 바인딩하고, 인증 없이 1인 1인스턴스로 동작합니다.

자세한 설계는 [`docs/spec/01-architecture.md`](docs/spec/01-architecture.md) 를 참고해 주십시오.

---

## 안전 원칙

| 원칙 | 내용 |
|---|---|
| 에이전트는 제출하지 않습니다 | 클릭 분류·strict 네트워크 모드·사후 감지 등 여러 겹의 결정적 코드로 막고, 실패하면 막는 쪽으로 닫힙니다. 가이드·프롬프트·LLM 출력으로 하네스를 풀 수 없습니다 |
| 안전 모드가 기본값입니다 | `submit_mode=dry_run` 이 기본이며, `live` 전환은 사용자가 UI 에서 명시적으로 확인해야 합니다 |
| 인증은 항상 사람이 합니다 | CAPTCHA·SMS·본인인증을 우회하지 않고, 계정을 대신 만들지 않으며, 비밀번호를 저장하거나 입력하지 않습니다 |
| 없는 사실을 쓰지 않습니다 | 이력서·자소서 생성물은 `ground_check` 로 프로필 근거에 묶입니다 |
| 고유식별정보를 저장하지 않습니다 | 주민등록번호 등은 필요할 때마다 묻고, 답변 KB 에도 남기지 않습니다 |

---

## 빠른 시작 (개발)

사전 요구사항: Python 3.12, [`uv`](https://docs.astral.sh/uv/), Node.js(웹 콘솔), Google Chrome.

```bash
make setup          # 의존성 설치 + .env 생성
uv run auto-apply   # 서버 기동 (127.0.0.1, 기동 시 DB 마이그레이션 자동 적용)
```

`--port 0` 을 주면 빈 포트를 골라 `auto-apply ready: http://127.0.0.1:<port>` 를 출력합니다. 상태 확인은 `GET /health` 입니다.

| 명령 | 용도 |
|---|---|
| `make check` | lint + type + arch + test — 커밋 전 필수 게이트 |
| `make test-fast` | 개발 중 빠른 반복 (첫 실패에서 멈춤, 게이트 아님) |
| `make test-all` | `native` 마커 포함 전체 (Chrome·claude CLI 필요) |
| `make fmt` | 포매팅 |
| `make api` | FastAPI 개발 서버 (`--reload`, 포트 8000) |
| `make web` | 웹 콘솔 dev 서버 (`make api` 가 먼저 떠 있어야 합니다) |

> 웹 콘솔은 M1 에서 재구성되기 전까지 동작하지 않습니다.

---

## 문서

- [`docs/spec/STATUS.md`](docs/spec/STATUS.md) — 현재 마일스톤과 다음 태스크
- [`docs/spec/00-product.md`](docs/spec/00-product.md) — 제품 스펙, 결정(D1~D16), 절대 규칙
- [`docs/spec/01-architecture.md`](docs/spec/01-architecture.md) — 아키텍처(§A1~§A10)
- [`docs/spec/02-milestones.md`](docs/spec/02-milestones.md) — 마일스톤과 태스크 카드
- [`CLAUDE.md`](CLAUDE.md) — 개발 규칙(계층·테스트·커밋)
