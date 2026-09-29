"""auto-apply v3 — 로컬 설치형 구직 지원 도구 (docs/spec/00-product.md)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("auto-apply")
except PackageNotFoundError:  # 설치 없이 소스 트리에서 import 할 때
    __version__ = "0.0.0"
