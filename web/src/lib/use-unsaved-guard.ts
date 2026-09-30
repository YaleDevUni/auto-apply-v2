import { useBlocker } from "@tanstack/react-router";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

// 저장하지 않은 편집이 있으면 화면 이동·탭 닫기 전에 묻는다. 돌려주는 함수는 "저장했으니 이번 이동은 묻지 말라"
// — 저장 직후 같은 콜백에서 navigate 하면 dirty 가 아직 이전 렌더 값이라서 필요하다.
export function useUnsavedGuard(dirty: boolean): () => void {
  const { t } = useTranslation();
  const allowed = useRef(false);
  useBlocker({
    shouldBlockFn: () => dirty && !allowed.current && !window.confirm(t("common.unsavedLeave")),
    enableBeforeUnload: () => dirty && !allowed.current,
  });
  return () => {
    allowed.current = true;
  };
}
