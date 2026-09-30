"""짐 매니페스트(§A4) — 사이트 목록·엔드포인트·파일 일관성과 잘못된 매니페스트 거부."""

import re

import pytest
from pydantic import ValidationError

from tests.gym.drivers import DRIVERS
from tests.gym.manifest import SITES_DIR, Manifest, load_manifest

# 카드 T2.3 이 요구하는 사이트 — 하나라도 빠지면 하네스(T2.5)가 그 경로를 시험하지 않게 된다
_API_PATH = r"/api/[a-z_]+/[a-z0-9_]+"

REQUIRED_SITES = {
    "spa_fetch",
    "multipart_form",
    "confirm_dialog",
    "open_form_button",
    "multi_step",
    "multi_step_form",
    "iframe_form",
    "plain_confirm_button",
    "beacon",
    "request_submit",
    "complete_page",
    "login_wall",
    # T2.5 이관 설계 과제: 링크로 여는 폼·GET 제출·confirm 뒤 제출·지연 제출·체크박스 onchange 제출
    "apply_link",
    "get_submit",
    "confirm_next",
    "delayed_submit",
    "consent_check",
    # T2.7(D17): 마지막 "다음" 이 최종 제출(신호 있음·없음), 검토 페이지형
    "next_final_signal",
    "next_final_nosignal",
    "review_page",
}


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return load_manifest()


def _site_text(name: str) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (SITES_DIR / name).glob("*.html"))


def test_required_sites_present(manifest):
    assert set(manifest.sites) >= REQUIRED_SITES


def test_every_site_has_a_positive_control_driver(manifest):
    assert set(DRIVERS) == set(manifest.sites)


def test_entries_and_redirects_exist(manifest):
    def file_of(url_path: str):
        p = SITES_DIR / url_path.removeprefix("/sites/")
        return p / "index.html" if url_path.endswith("/") else p

    assert file_of(manifest.done_page).is_file()
    for site in manifest.sites.values():
        assert file_of(site.entry).is_file(), site.name
        for ep in (*site.final_submit, *site.allowed):
            if ep.redirect:
                assert file_of(ep.redirect).is_file(), ep.redirect
        if site.login:
            assert file_of(site.login.page).is_file()


def test_site_pages_actually_use_their_endpoints(manifest):
    # 매니페스트와 HTML 이 어긋나면 "제출 0건"이 엉뚱한 경로를 센다
    for site in manifest.sites.values():
        text = _site_text(site.name)
        for ep in (*site.final_submit, *site.allowed):
            assert ep.path in text, f"{site.name}: {ep.path} 를 쓰는 페이지가 없다"
        assert re.findall(_API_PATH, text), site.name
        for used in set(re.findall(_API_PATH, text)):
            assert any(manifest.endpoint(m, used) for m in ("POST", "GET")), (
                f"{site.name}: 매니페스트에 없는 {used}"
            )


def test_site_files_never_reference_external_hosts():
    for page in SITES_DIR.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert not re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}", text, re.I), page


def test_lookup(manifest):
    assert manifest.site_of("/api/beacon/submit") == "beacon"
    assert manifest.site_of("/api/nope/submit") is None
    assert manifest.site_of("/sites/beacon/") is None
    ep, final = manifest.endpoint("POST", "/api/multi_step/save")
    assert final is False and ep.path == "/api/multi_step/save"
    assert manifest.endpoint("POST", "/api/multi_step/submit")[1] is True
    assert manifest.endpoint("PUT", "/api/multi_step/submit") is None
    assert manifest.endpoint("GET", "/api/multi_step/submit") is None


def _one(**site) -> dict:
    base = {
        "title": "t",
        "entry": "/sites/s/",
        "final_submit": [{"method": "POST", "path": "/api/s/x"}],
    }
    return {"done_page": "/sites/done.html", "sites": {"s": {**base, **site}}}


@pytest.mark.parametrize(
    "bad",
    [
        _one(final_submit=[]),
        _one(allowed=[{"method": "GET", "path": "/api/s/y"}]),
        _one(final_submit=[{"method": "OPTIONS", "path": "/api/s/x"}]),
        _one(final_submit=[{"method": "POST", "path": "/api/other/x"}]),
        _one(final_submit=[{"method": "POST", "path": "/submit"}]),
        _one(allowed=[{"method": "POST", "path": "/api/s/x"}]),
        _one(entry="/sites/other/"),
        _one(unknown_key=1),
        _one(accepted_risk="daring"),  # 받아들인 위험은 결정 번호(D…)로만
        _one(
            login={
                "cookie": "c",
                "prefix": "/sites/s/",
                "page": "/sites/s/l.html",
                "endpoint": "/api/s/l",
            }
        ),
        _one(
            login={
                "cookie": "c",
                "prefix": "/sites/s/",
                "page": "/sites/x/l.html",
                "endpoint": "/api/s/l",
            },
            allowed=[{"method": "POST", "path": "/api/s/l"}],
        ),
    ],
)
def test_rejects_bad_manifest(bad):
    with pytest.raises(ValidationError):
        Manifest.model_validate(bad)


def test_minimal_manifest_ok():
    m = Manifest.model_validate(_one())
    assert m.sites["s"].name == "s"


def test_get_final_submit_is_allowed_for_get_submission_sites():
    m = Manifest.model_validate(_one(final_submit=[{"method": "GET", "path": "/api/s/x"}]))
    assert m.endpoint("GET", "/api/s/x") is not None


def test_accepted_risk_sites_are_only_the_d17_no_signal_site(manifest):
    # 하네스가 있어도 제출이 나가는 사이트는 설계 결정으로만 — 늘어나면 리뷰에서 보이게
    risky = {n: s.accepted_risk for n, s in manifest.sites.items() if s.accepted_risk}
    assert risky == {"next_final_nosignal": "D17"}
