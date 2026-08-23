# 열려 있던 결정들

> `docs/ARCHITECTURE.md` 색인의 §9. 절 번호는 코드 주석이 참조하므로 바뀌지 않는다.

---

## 9. 대화에서 열려 있던 결정들 — 내 권고

### 9.1 DB: SQLite로 시작 vs Postgres로 시작 → **Postgres 권고**
대화의 "SQLite로 시작해도 된다"는 옳지만, 그 근거인 *단순함*이 이 스택에서는 성립하지 않는다.
Temporal을 쓰는 순간 이미 `docker-compose`를 띄운다. 컨테이너가 이미 있으면 Postgres 추가 비용은
거의 0이고, 반대로 SQLite로 가면 (a) 워커 3개의 쓰기 경합, (b) `JSONB` 없이 `raw`/`spec`/`profile`
질의, (c) 나중 마이그레이션 — 세 가지를 나중에 갚아야 한다.
단, **Repository 계층은 그대로 둔다** (테스트에서 SQLite in-memory를 쓸 수 있고 결합도가 낮아진다).

### 9.2 오케스트레이션 프레임워크(LangGraph 등): 지금 필요한가 → **M3에도 여전히 보류**
"생성 → 검토 → 재생성" 루프만이라면 Temporal이 이미 루프·상태·재시도를 준다. 먼저 넣으면
**두 개의 오케스트레이터를 동시에 디버깅**하게 되는데, 그게 정확히 V1의 실패 모드다.
경계(`run_resume_graph(input) -> ResumeDraft`, 실제로는 `ports/resume.py`의 `ResumeGenerator`/
`ResumeReviewer`)만 확정해 두면, 그래프가 실제로 분기·병렬·조건부 재작성으로 복잡해지는 시점에
내부 구현만 갈아끼울 수 있다.

M3에서 Fact 기반 생성(retrieve_facts → select_relevant_facts → generate → ground_check)을
실제로 구현하면서 이 판단을 재확인했다: 파이프라인이 여전히 분기·병렬 없는 선형 체인이라 도입
기준을 못 채운다. 그래서 plain 함수(`adapters/resume/simple.py`)로 남겨뒀다. 후보도 LangGraph로
못박지 않는다 — 소규모 프로젝트에는 PydanticAI 쪽이 더 맞을 수 있어 그것도 함께 검토 중이다.
`ai/schemas.py`(순수 Pydantic)와 `ai/prompts.py`(순수 문자열 함수)를 어느 프레임워크의 타입에도
묶지 않은 이유가 이것 — 나중에 `LangGraphResumeGenerator`든 `PydanticAIResumeGenerator`든 같은
스키마를 그대로 재사용하며 포트 뒤에서 교체할 수 있다.

### 9.3 Recipe 자동 승격 → **금지 (supervised 1회 필수)**
대화의 흐름은 "Sandbox PASS → 저장"이었다. 그런데 dry-run은 submit을 하지 않으므로
**submit 경로의 정확성을 증명하지 못한다**(§2.4의 submit 대상 확인은 "그 버튼이 거기 있다"까지만
증명한다 — 눌렀을 때 실제로 접수되는지는 여전히 못 본다). 그래서 `candidate` 첫 실행은 사람 확인이 붙는 supervised 모드.
"사람 확인이 붙는다"는 정책은 §2.4c에서 `CheckpointWaiter`(페이지 경계마다 스크린샷 승인,
SUBMIT은 항상 강제)로 실제 구현됐다.

### 9.4 관측성 → 처음부터 최소한만
Grafana 스택 전체를 초기에 세우지 않는다. 대신 **Temporal UI를 1차 운영 콘솔로 쓰고**,
모든 로그에 `workflow_id`를 구조화 필드로 넣는다. OTel exporter는 워커 부트스트랩에 자리만 만들고
실제 백엔드 연결은 M4에서.

### 9.5 법적/정책 리스크 (설계 제약으로 반영)
자동 지원은 플랫폼 ToS와 충돌할 수 있다. 그래서 설계에 다음을 **기능이 아니라 제약으로** 넣었다:
`platform_policies`의 일일 상한/최소 간격, 플랫폼당 동시성 1, CAPTCHA 우회 금지, 최종 제출은
사람 승인 뒤에만. 규모를 키우는 방향(대량 자동 지원)이 아니라 **본인 지원 건을 정확하게 처리하는**
방향으로 설계했다.
