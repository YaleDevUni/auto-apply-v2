# Runbook

## M1 수동 검증 — "워커를 죽여도 예약이 살아있는가"

자동화된 증명은 `tests/workflows/test_durability.py`에 있다 (Temporal test 환경 + 워커 재기동).
아래는 **실제 서버에서 프로세스를 강제 종료**해 같은 것을 눈으로 확인하는 절차다.

```bash
make up                      # postgres / temporal / temporal-ui / minio
```

터미널 3개에 워커를 띄운다 (큐마다 자원 특성이 달라 분리한다, §1):

```bash
QUEUE=default make worker
```

```bash
QUEUE=ai make worker
```

```bash
QUEUE=browser make worker
```

지원 워크플로우를 시작하고 승인 대기까지 진행:

```bash
uv run python -m auto_apply.cli start app_1 --job-url https://fixture.local/jobs/1
```

```bash
uv run python -m auto_apply.cli status app_1
```

`awaiting_approval`이 보이면 5분 뒤로 예약 승인:

```bash
uv run python -m auto_apply.cli approve app_1 --at 2026-08-20T09:00
```

**여기서 세 워커를 모두 `Ctrl+C`(또는 `kill -9`)로 죽인다.**
Temporal UI(http://localhost:8080)에서 워크플로우는 여전히 Running이고 타이머가 살아있다.

워커를 다시 띄우면 예약 시각에 실행이 이어진다. 상태는 워커가 아니라 서버에 있기 때문이다.

```bash
uv run python -m auto_apply.cli status app_1
```

### 확인 포인트
- 워커가 죽은 동안 보낸 승인 signal이 유실되지 않는다 (서버가 보관한다)
- 예약 시각까지 남은 시간이 워커 재시작으로 초기화되지 않는다
- `/status`는 DB가 아니라 **워크플로우 query**를 읽는다 (§4.1)

## 자주 쓰는 조작

```bash
uv run python -m auto_apply.cli schedule app_1 --at 2026-08-21T10:00
```

```bash
uv run python -m auto_apply.cli cancel app_1
```

```bash
uv run python -m auto_apply.cli reject app_1 --reason "회사 부적합"
```

## 안전장치

`.env`의 `DRY_RUN_ONLY=true`가 기본값이다. 이 값이 true면 executor가 최종 submit 직전에 멈춘다.
실제 제출을 켤 때는 반드시 (1) recipe가 `active`이고 (2) supervised 모드로 1회 확인한 뒤에 한다 (§2.4).
