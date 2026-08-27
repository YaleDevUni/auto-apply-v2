# Runbook

운영 중 실제로 하는 조작과, 문제가 났을 때 보는 순서. 설계 근거는
[`ARCHITECTURE.md`](ARCHITECTURE.md) 색인에서 해당 절로 간다.

---

## 1. 상시 띄워두는 프로세스

인프라만 Docker, **api/worker 등은 호스트에서 uv로** 실행한다(디버깅 편의).

**기본은 한 번에 다 띄운다** — 터미널을 나눠 하나씩 켜다 보면 리스너가 조용히 빠지는 사고가
있었다(2026-08-28). `scripts/dev_up.sh`가 인프라 확인 → 마이그레이션 → 워커×3/api/
telegram-listener/watchdog 6개를 백그라운드로 기동하고, 하나라도 뜬 직후 죽으면 그것만
콕 집어 실패로 보고한다(전체를 성공으로 보고하지 않는다):

```bash
make dev-up                  # 인프라+마이그레이션+워커×3+api+telegram-listen+watchdog
make dev-status              # 6개 프로세스 + docker compose ps 상태
make dev-down                # 정지 (ARGS="--infra" 로 docker 인프라까지 같이)
```

로그는 `logs/<name>.log`, pid 는 `logs/pids/<name>.pid` — 이미 떠 있는 프로세스는 pidfile로
감지해 중복 기동하지 않으므로, 실패한 것만 있어도 `dev-up`을 그냥 다시 돌리면 된다.

개별 프로세스를 포그라운드에 붙잡고 디버깅할 때만 터미널을 나눠 쓴다(큐마다 자원 특성이
달라 원래도 분리돼 있었다, §1):

```bash
make up && make migrate      # 인프라(postgres/temporal/temporal-ui/minio) + 마이그레이션
```

```bash
QUEUE=default make worker    # 오케스트레이션 + DB
QUEUE=ai make worker         # LLM — 느리고 토큰 비용이 있다
QUEUE=browser make worker    # Playwright — 메모리 수백 MB/세션, 플랫폼당 동시성 1
```

```bash
make telegram-listen         # 승인 버튼을 받는다. 이게 죽으면 버튼이 조용히 안 먹는다
make watchdog                # 실패/종료 감시 (§11.2d)
make api                     # FastAPI (웹훅을 쓸 때만 필수)
```

**세 상주 프로세스(worker/listener/watchdog)가 크래시하면 텔레그램으로 알림이 온다**
(`process_alerts.run_guarded`, §11.2d). `Ctrl+C`나 설정 오류로 인한 조기 종료는 사고가
아니라 알리지 않는다 — 알림이 없다고 살아있다는 뜻은 아니므로, 조용해졌으면 `make dev-status`로
프로세스 목록을 먼저 본다.

---

## 2. 자주 쓰는 조작

### 지원 시작·제어 (CLI)

```bash
uv run python -m auto_apply.cli start app_1 --job-url https://www.wanted.co.kr/wd/123456
uv run python -m auto_apply.cli status app_1        # DB 가 아니라 워크플로우 query (§4.1)
uv run python -m auto_apply.cli approve app_1 --at 2026-08-25T09:00
uv run python -m auto_apply.cli reject  app_1 --reason "회사 부적합"
uv run python -m auto_apply.cli schedule app_1 --at 2026-08-26T10:00
uv run python -m auto_apply.cli cancel app_1
```

### 공고 수집·자동 지원

```bash
uv run python -m auto_apply.cli collect --platforms wanted,saramin   # 수동 1회
uv run python -m auto_apply.cli collect-schedule                     # cron 등록 (idempotent)
uv run python -m auto_apply.cli collect-unschedule
uv run python -m auto_apply.cli apply-schedule                       # 자동 지원 시작 cron
uv run python -m auto_apply.cli apply-unschedule
```

`collect-schedule`/`apply-schedule`은 **처음 배포할 때 한 번**만 실행한다 — DB
(`schedule_configs`)를 `.env` 시드값으로 채우는 용도다. 그 뒤로 시각·건수·on/off 는 전부
텔레그램 채팅으로 바꾼다(§11.2f).

### 승인이 안 왔을 때

```bash
uv run python -m auto_apply.cli resend-pending    # 대기 중인 지원 건 버튼 일괄 재전송 (§11.2g)
```

리스너가 죽어 있던 동안 눌린 버튼은 유실되지만 **워크플로우 쪽 nonce 는 살아있다**. 재기동
직후 이 명령 하나로 복구한다. `NOTIFIER=telegram` 이 아니면 안내만 하고 끝난다.

### 텔레그램 채팅으로 하는 것들

슬래시 명령은 없다. 그냥 말하면 된다(§6) — "3건 지원해줘", "이 링크 지원해줘 `<url>`",
"이거 다시 시도해줘 `<application_id>`", "지금 공고 수집해줘", "스케줄 상태 알려줘",
"자동지원 오후 2시 5건으로 바꿔줘", "대기 중인 거 다시 보여줘".

### 첨부파일 정리

```bash
make resume-cleanup                  # 기본 dry-run — 후보만 출력
make resume-cleanup ARGS="--yes"     # 실제 삭제
```

지원 1회 = 플랫폼 계정에 남는 이력서 파일 1개다(§11.2e). 삭제는 되돌릴 수 없어 상시 자동화
하지 않는다 — 사람이 후보 목록을 보고 `--yes` 를 붙인다.

---

## 3. 안전장치 — 실제 제출 전에 확인할 것

`.env`의 `DRY_RUN_ONLY=true`가 기본값이다. 이 값이 true면 executor 가 최종 submit 직전에 멈춘다.

실제 제출을 켤 때의 순서:

1. recipe 가 `active` 인지 확인 — `GET /recipes/{platform}`(§7), 채팅으로는
   "wanted recipe 버전 보여줘"(`list_recipe_versions` 도구). 승격은
   `POST /recipes/{platform}/promote` 또는 수선 워크플로우의 텔레그램 승격 승인으로만 한다 —
   `var/recipes/*.json` 의 `status` 를 손으로 고치지 않는다(§2.4).
2. `SUPERVISED` 모드로 1회 확인 — 페이지 경계마다 스크린샷 승인이 붙는다(§2.4c).
   `SUBMIT` 은 recipe 에 플래그가 없어도 **항상** 체크포인트가 걸린다.
3. 그 다음에 `DRY_RUN_ONLY=false`.

**승인 메시지 맨 앞의 배지를 반드시 본다** — 🧪 DRY RUN / ⚠️ SUPERVISED / 🚨 LIVE /
❓ 확인 불가. dry-run 인 줄 알았는데 recipe 가 이미 승격돼 실제로 제출되는 사고를 막으려고
붙인 것이다(§6). "❓ 확인 불가"는 recipe 조회가 실패했다는 뜻이지 안전하다는 뜻이 아니다.

---

## 4. 문제가 났을 때 보는 순서

| 증상 | 먼저 볼 것 |
|---|---|
| 지원이 조용히 멈춰 있다 | Temporal UI(localhost:8080) → 해당 워크플로우가 Running 인지. Running 이면 worker 가 죽었을 가능성이 크다(watchdog 은 *닫힌* 워크플로우만 본다, §11.2d) |
| 승인 버튼을 눌러도 반응이 없다 | `make dev-status`로 listener 프로세스 확인 → 죽어 있으면 `make dev-up`으로 재기동 → `cli resend-pending` 으로 복구 |
| 공고가 하나도 안 잡힌다 | `JOB_COLLECTION_UNHEALTHY` 알림이 왔는지. 셀렉터가 바뀌면 예외 없이 `found=0` 이 된다(§11.2d 1번) |
| 이력서 생성이 실패한다 | `LLMAuthRequired`(claude CLI 로그인 풀림) / `LLMQuotaExceeded`(구독 한도) 알림 — 둘 다 재시도로 안 풀린다. 해결 후 `retry_application` (§11.2c) |
| 실행이 계속 같은 자리에서 실패한다 | 수선 워크플로우가 이미 돌고 있는지(`repair-{platform}-{form_hash}`). recipe DOM 문제가 아니라 timeout 일 수도 있다 — 실패 사유에 `Timeout \d+ms exceeded` 가 있으면 셀렉터 문제가 아니다(§2.4) |
| 로그인 세션이 만료됐다 | `AuthRequired`. `scripts/auto_login.py`(자동, 본인 계정 자격증명) 또는 `scripts/save_auth_state.py`(수동). CAPTCHA 를 만나면 둘 다 중단하고 사람에게 넘긴다 — 우회하지 않는다(§3) |

**모든 로그에 `workflow_id` 가 구조화 필드로 들어있다.** DB·S3·Temporal 히스토리를 잇는
유일한 키다(§4.1) — 조사할 땐 이 값부터 잡는다.

---

## 5. M1 수동 검증 — "워커를 죽여도 예약이 살아있는가"

자동화된 증명은 `tests/workflows/test_durability.py`에 있다(Temporal test 환경 + 워커 재기동).
아래는 **실제 서버에서 프로세스를 강제 종료**해 같은 것을 눈으로 확인하는 절차다. Temporal 을
도입한 이유 자체를 확인하는 절차라 남겨둔다.

지원 워크플로우를 시작하고 승인 대기까지 진행한 뒤 `awaiting_approval` 이 보이면 5분 뒤로
예약 승인하고, **세 워커를 모두 `Ctrl+C`(또는 `kill -9`)로 죽인다.**

Temporal UI에서 워크플로우는 여전히 Running 이고 타이머가 살아있다. 워커를 다시 띄우면 예약
시각에 실행이 이어진다 — 상태가 워커가 아니라 서버에 있기 때문이다.

### 확인 포인트
- 워커가 죽은 동안 보낸 승인 signal 이 유실되지 않는다 (서버가 보관한다)
- 예약 시각까지 남은 시간이 워커 재시작으로 초기화되지 않는다
- `status` 는 DB 가 아니라 **워크플로우 query** 를 읽는다 (§4.1)

> 워커 재시작을 **테스트 코드로** 검증할 땐 `Worker(..., max_cached_workflows=0)` 이 필요하다.
> sticky execution 이 켜져 있으면 서버가 죽은 워커의 sticky 큐로 계속 라우팅해서 테스트가
> 에러도 없이 그냥 멈춘다.
