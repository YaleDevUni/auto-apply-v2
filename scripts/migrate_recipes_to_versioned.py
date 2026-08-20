"""var/recipes/ 를 옛 flat 레이아웃에서 버전 디렉토리 레이아웃으로 1회성 이관한다.

M4 phase 1(recipe 버전관리 write path) 작업 — `{root}/{platform}.json`(파일 하나 = recipe
하나, 버전 이력 없음) 을 `{root}/{platform}/{version}.json`(버전마다 파일 하나, append-only
이력) 으로 옮긴다. `JsonFileRecipeSource` 가 이 디렉토리 레이아웃을 전제하므로, 이 스크립트를
안 돌리면 기존 active recipe 를 못 찾아 실행이 깨진다.

이미 디렉토리 레이아웃으로 옮겨진 platform 은 건너뛴다 — 여러 번 돌려도 안전하다.

사용법:
    uv run python scripts/migrate_recipes_to_versioned.py [--data-dir var]
"""

import argparse
import json
from pathlib import Path


def migrate(data_dir: Path) -> None:
    root = data_dir / "recipes"
    flat_files = sorted(root.glob("*.json"))
    if not flat_files:
        print("옮길 flat recipe 파일이 없다 — 이미 이관됐거나 애초에 없음")
        return

    for path in flat_files:
        platform = path.stem
        target_dir = root / platform
        if target_dir.exists():
            print(f"{platform}: 이미 버전 디렉토리가 있다 — 건너뜀 ({path} 는 그대로 둠)")
            continue

        raw = path.read_text()
        version = _extract_version(raw)
        target_dir.mkdir(parents=True)
        (target_dir / f"{version}.json").write_text(raw)
        print(f"{platform}: {path} -> {target_dir / f'{version}.json'}")

    print("원본 flat 파일은 백업 겸 지우지 않았다 — 확인 후 필요하면 손으로 정리")


def _extract_version(raw: str) -> int:
    return int(json.loads(raw)["version"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("var"))
    args = parser.parse_args()
    migrate(args.data_dir)


if __name__ == "__main__":
    main()
