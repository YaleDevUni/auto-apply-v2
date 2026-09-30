"""Composition root (§A2) — 어댑터를 만들고 잇는 유일한 곳. 책임별 모듈:

- `data`: 데이터 디렉터리·마이그레이션·설치별 토큰 (기동 전)
- `adapters`: 설정값 → 어댑터 하나씩
- `agent`: 브라우저 짝·에이전트 런타임 선택·run 한도·앱 출처
- `container`: 전부 잇기
"""

from auto_apply.bootstrap.agent import BrowserPair
from auto_apply.bootstrap.container import Container, build_container
from auto_apply.bootstrap.data import StartupError, ensure_session_token, prepare_data_dir

__all__ = [
    "BrowserPair",
    "Container",
    "StartupError",
    "build_container",
    "ensure_session_token",
    "prepare_data_dir",
]
