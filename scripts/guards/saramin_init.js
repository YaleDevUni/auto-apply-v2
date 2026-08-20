// 사람인 라이브 탐색 세션 전용 init-script (scripts/explore_platform.sh 가 자동 로드한다).
//
// 배경(2026-08-21 사고): recipe-builder가 "즉시지원" 버튼(onclick="quickApplyForm(rec_idx,...)")을
// 눌렀는데, 사람인의 check-open-layer-condition API가 계정 상태(이미 기본 이력서가 선택돼 있는 등)에
// 따라 확인 레이어를 건너뛰고 곧장 실제 제출까지 진행했다. "최종 제출 버튼은 누르지 않는다"는
// 규칙은 이 사고를 못 막았다 — 겉보기엔 안전한 첫 클릭이 플랫폼 쪽 조건부 로직 때문에 곧장
// 마지막 액션이 됐기 때문이다. 그래서 규칙이 아니라 이 진입점 자체를 코드로 무력화한다.
//
// scripts/explore_platform.sh 의 네트워크 가드(apply/submit 경로 xhr·fetch abort)와 이중 방어.
// 새로운 위험 진입점을 발견하면 이 파일에 추가해라 — 덮어쓰지 말고 이어 붙인다.

window.quickApplyForm = function () {
  console.warn("[explore-guard] quickApplyForm 호출이 차단됐다 — recipe-builder 탐색 세션에서는 비활성화됨");
};
