"""테스트 짐 매니페스트(§A4) — 사이트별 최종 제출 요청·허용 중간 요청.

매니페스트가 틀리면 "제출 0건" 단언이 엉뚱한 엔드포인트를 세게 된다 —
그래서 로드할 때 엄격히 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SITES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sites"
MANIFEST_PATH = SITES_DIR / "manifest.yaml"

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Endpoint(_Frozen):
    method: str
    path: str = Field(pattern=r"^/api/[a-z_]+/[a-z0-9_]+$")
    redirect: str | None = Field(default=None, pattern=r"^/sites/")

    @model_validator(mode="after")
    def _non_get(self) -> Endpoint:
        # L3 는 비-GET 을 막는다 — 짐의 기록 대상도 비-GET 이어야 그 층을 실제로 시험한다.
        if self.method not in _BODY_METHODS:
            raise ValueError(f"기록 대상은 비-GET 이어야 한다: {self.method} {self.path}")
        return self

    def matches(self, method: str, path: str) -> bool:
        return self.method == method and self.path == path


class Login(_Frozen):
    cookie: str = Field(min_length=1)
    prefix: str = Field(pattern=r"^/sites/.+/$")
    page: str
    endpoint: str

    @model_validator(mode="after")
    def _page_under_prefix(self) -> Login:
        if not self.page.startswith(self.prefix):
            raise ValueError("로그인 페이지는 보호 접두사 아래에 있어야 한다")
        return self


class Site(_Frozen):
    name: str
    title: str
    entry: str = Field(pattern=r"^/sites/")
    final_submit: tuple[Endpoint, ...] = Field(min_length=1)
    allowed: tuple[Endpoint, ...] = ()
    login: Login | None = None

    @model_validator(mode="after")
    def _namespaced(self) -> Site:
        prefix = f"/api/{self.name}/"
        for ep in (*self.final_submit, *self.allowed):
            if not ep.path.startswith(prefix):
                raise ValueError(f"{self.name}: 엔드포인트는 {prefix} 아래여야 한다 — {ep.path}")
        finals = {(e.method, e.path) for e in self.final_submit}
        if finals & {(e.method, e.path) for e in self.allowed}:
            raise ValueError(
                f"{self.name}: 같은 엔드포인트가 final_submit 과 allowed 에 동시에 있다"
            )
        if not self.entry.startswith(f"/sites/{self.name}/"):
            raise ValueError(f"{self.name}: entry 는 /sites/{self.name}/ 아래여야 한다")
        if self.login and not any(e.path == self.login.endpoint for e in self.allowed):
            raise ValueError(f"{self.name}: 로그인 엔드포인트는 allowed 에 있어야 한다")
        return self


class Manifest(_Frozen):
    done_page: str = Field(pattern=r"^/sites/")
    sites: dict[str, Site]

    @model_validator(mode="before")
    @classmethod
    def _inject_names(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("sites"), dict):
            sites = {
                name: {**spec, "name": name} if isinstance(spec, dict) else spec
                for name, spec in data["sites"].items()
            }
            data = {**data, "sites": sites}
        return data

    def site_of(self, path: str) -> str | None:
        """`/api/<site>/…` 요청이 어느 사이트 것인지."""
        parts = path.split("/")
        if len(parts) >= 3 and parts[1] == "api" and parts[2] in self.sites:
            return parts[2]
        return None

    def endpoint(self, method: str, path: str) -> tuple[Endpoint, bool] | None:
        """(엔드포인트, 최종 제출 여부). 매니페스트에 없으면 None."""
        site = self.site_of(path)
        if site is None:
            return None
        spec = self.sites[site]
        for ep in spec.final_submit:
            if ep.matches(method, path):
                return ep, True
        for ep in spec.allowed:
            if ep.matches(method, path):
                return ep, False
        return None


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    return Manifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
