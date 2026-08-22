import pytest

from auto_apply.domain.schedule_cron import build_cron


def test_build_cron_formats_minute_then_hour():
    assert build_cron(hour=14, minute=30) == "30 14 * * *"


def test_build_cron_defaults_ok_at_boundaries():
    assert build_cron(hour=0, minute=0) == "0 0 * * *"
    assert build_cron(hour=23, minute=59) == "59 23 * * *"


@pytest.mark.parametrize("hour", [-1, 24])
def test_build_cron_rejects_out_of_range_hour(hour):
    with pytest.raises(ValueError, match="hour"):
        build_cron(hour=hour, minute=0)


@pytest.mark.parametrize("minute", [-1, 60])
def test_build_cron_rejects_out_of_range_minute(minute):
    with pytest.raises(ValueError, match="minute"):
        build_cron(hour=0, minute=minute)
