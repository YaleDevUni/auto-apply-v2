"""에이전트가 열면 안 되는 주소 (§A5 navigate, 절대 규칙 1).

앱 자신의 웹 콘솔을 자동화 브라우저에서 열면 같은 출처가 되어 승인 API 를 부를 수 있다 — 에이전트가
자기 지원서를 승인하는 통로다. 루프백은 이름이 여럿(127.0.0.0/8·localhost·::1)이라 포트로 묶어 본다.
"""

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_key(url: str) -> tuple[str, int] | None:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().rstrip(".")
    scheme = parts.scheme.lower()
    if not host or scheme not in _DEFAULT_PORTS:
        return None
    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError:
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return "loopback", port
    try:
        if ipaddress.ip_address(host).is_loopback:
            return "loopback", port
    except ValueError:
        pass
    return host, port


def is_forbidden_url(url: str, forbidden_origins: Iterable[str]) -> bool:
    """`url` 이 금지 출처 중 하나와 같은 호스트(루프백은 한 묶음)·포트인가.

    해석 못 하는 주소는 금지로 본다.
    """
    key = _origin_key(url)
    if key is None:
        return True
    return any(key == _origin_key(origin) for origin in forbidden_origins)


def matches_origin(url: str, origins: Iterable[str]) -> bool:
    """페이지가 스스로 보낸 요청이 금지 출처로 가는가 (§A4 L3 route, T2.4 이관).

    `is_forbidden_url` 과 달리 http(s) 가 아닌 주소(data:·blob: 등)는 출처가 없어 해당 없음으로
    본다 — 그런 요청은 앱에 닿지 않는다.
    """
    key = _origin_key(url)
    return key is not None and any(key == _origin_key(origin) for origin in origins)
