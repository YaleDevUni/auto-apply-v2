"""select_deletable / is_deletable_title — wanted-resume-list-cleanup-backlog 회귀 테스트."""

from datetime import UTC, datetime, timedelta

from auto_apply.contracts.dto import ResumeAttachment
from auto_apply.domain.resume_cleanup import is_deletable_title, select_deletable

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _attachment(
    key: str, title: str, *, content_type: str = "application/pdf", age: timedelta
) -> ResumeAttachment:
    return ResumeAttachment(key=key, title=title, content_type=content_type, updated_at=NOW - age)


def test_is_deletable_title_matches_generated_resume_pattern():
    assert is_deletable_title("res_2942bf8c75c249e7.pdf") is True


def test_is_deletable_title_matches_known_test_artifact():
    assert is_deletable_title("recipe-test-dummy.pdf") is True


def test_is_deletable_title_rejects_hand_named_files():
    """사람이 직접 지은 이름은 우연히 비슷해도 건드리지 않는다 — 정확한 정규식만 통과."""
    assert is_deletable_title("res_report.pdf") is False
    assert is_deletable_title("박예일클라우드이력서.pdf") is False


def test_select_deletable_excludes_portfolio_and_hand_named_files():
    """포트폴리오/직접 업로드 파일은 이름이 고정돼 있어 여러 지원에 재사용된다 — 보존."""
    attachments = [
        _attachment("k1", "res_2942bf8c75c249e7.pdf", age=timedelta(days=1)),
        _attachment("k2", "박예일_포트폴리오_데브옵스.pdf", age=timedelta(days=30)),
        _attachment("k3", "박예일클라우드이력서.pdf", age=timedelta(days=30)),
    ]
    result = select_deletable(attachments, now=NOW)
    assert [a.key for a in result] == ["k1"]


def test_select_deletable_excludes_wanted_native_resumes():
    """content_type == wanted/resume 은 이 프로젝트가 만들지 않는 문서라 손대지 않는다."""
    attachments = [
        _attachment("k1", "박예일 기본", content_type="wanted/resume", age=timedelta(days=30))
    ]
    assert select_deletable(attachments, now=NOW) == []


def test_select_deletable_protects_recent_files_within_min_age():
    """방금 올라온 파일은 아직 실행 중인 워크플로우가 쓰고 있을 수 있다 — age 버퍼로 보호."""
    attachments = [_attachment("k1", "res_2942bf8c75c249e7.pdf", age=timedelta(minutes=30))]
    assert select_deletable(attachments, now=NOW, min_age=timedelta(hours=2)) == []
    assert select_deletable(attachments, now=NOW, min_age=timedelta(minutes=10)) == [attachments[0]]


def test_select_deletable_boundary_is_inclusive():
    attachments = [_attachment("k1", "res_2942bf8c75c249e7.pdf", age=timedelta(hours=2))]
    assert select_deletable(attachments, now=NOW, min_age=timedelta(hours=2)) == attachments
