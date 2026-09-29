"""JobRunner 수명주기 (§A9). 큐 소비는 M3 에서 붙는다 — 지금은 기동·정지만."""

from auto_apply.runner.job_runner import JobRunner


async def test_start_and_stop():
    runner = JobRunner()
    assert runner.running is False
    await runner.start()
    assert runner.running is True
    await runner.stop()
    assert runner.running is False


async def test_start_is_idempotent_and_restartable():
    runner = JobRunner()
    await runner.start()
    await runner.start()
    await runner.stop()
    await runner.stop()
    await runner.start()
    assert runner.running is True
    await runner.stop()
