#!/usr/bin/env bash
# recipe-builder 라이브 탐색 세션을 안전 가드와 함께 연다 — 반드시 이 스크립트로 세션을 열고,
# agent-browser open 을 직접 호출하지 않는다.
#
# 배경(2026-08-21 사고, 사람인): "즉시지원" 버튼 클릭이 확인 레이어 없이 곧장 실제 제출로
# 이어졌다(케이플 오지원, 사람이 취소했지만 고용주 알림 메일은 회수 안 됨). "최종 제출 버튼은
# 누르지 않는다"는 서면 규칙만으로는 이런 사고를 못 막는다 — 플랫폼 쪽 조건부 로직이 무해해
# 보이는 첫 클릭을 곧장 마지막 액션으로 만들 수 있어서다. 그래서 규칙이 아니라 네트워크/JS
# 계층에서 강제로 막는다:
#   1) apply/submit 이 URL 경로에 들어간 xhr·fetch 요청을 기본 차단(document 탐색/goto 는
#      resource-type 밖이라 안 막힌다 — 지원폼 URL로 직접 이동하는 건 여전히 가능).
#   2) scripts/guards/{platform}_init.js 가 있으면 그 페이지의 알려진 위험 JS 진입점(예:
#      사람인 quickApplyForm)을 로드 시점에 무력화한다.
#
# 사용법: scripts/explore_platform.sh <platform> <url>
set -euo pipefail

platform="${1:?사용법: scripts/explore_platform.sh <platform> <url>}"
url="${2:?사용법: scripts/explore_platform.sh <platform> <url>}"
session="${platform}-explore"
init_script="$(dirname "$0")/guards/${platform}_init.js"

open_args=(--session-name "$session" open --headed)
if [[ -f "$init_script" ]]; then
  open_args+=(--init-script "$init_script")
fi

agent-browser "${open_args[@]}"
agent-browser --session-name "$session" network route "**/*apply*" --abort --resource-type xhr,fetch
agent-browser --session-name "$session" network route "**/*submit*" --abort --resource-type xhr,fetch
agent-browser --session-name "$session" navigate "$url"

echo "[explore_platform] 네트워크 가드 활성화: apply/submit 경로 xhr·fetch abort"
if [[ -f "$init_script" ]]; then
  echo "[explore_platform] init-script 적용됨: ${init_script}"
else
  echo "[explore_platform] ${platform}용 init-script 없음 — 위험한 JS 진입점(예: onclick 핸들러가"
  echo "  곧장 제출로 이어지는 함수)을 발견하면 ${init_script} 를 만들어 무력화하고, 다음"
  echo "  탐색에서도 재사용되게 남겨둬라."
fi
