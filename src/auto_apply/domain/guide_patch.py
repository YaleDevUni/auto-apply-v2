"""가이드 전문 치환 — 순수 함수 (ARCHITECTURE.md, 메모리 resume-revise-feedback-design).

LLM 에게 `config/resume_guide.md` 전문을 다시 쓰게 하면 지시하지 않은 다른 규칙이 조용히
사라질 수 있다. 그래서 LLM 은 `{old, new}` 치환 쌍만 내고, 실제 텍스트 교체는 이 결정론
함수가 한다 — Recipe 와 같은 "AI 는 생성만, 조합·적용은 코드" 철학의 연장.
"""

from collections.abc import Sequence

from auto_apply.domain.errors import GuidePatchAmbiguous, GuidePatchNotFound


def apply_patch(text: str, old: str, new: str) -> str:
    """`text` 안에서 `old`가 정확히 1번만 나타날 때만 `new`로 바꾼다.

    0번이면 `GuidePatchNotFound`, 2번 이상이면 `GuidePatchAmbiguous` — 둘 다 "어디를 바꿀지
    확신할 수 없다"는 뜻이라 재시도 없이 사람에게 넘긴다. `old`가 빈 문자열이면 "기존 규칙은
    안 건드리고 새 규칙을 추가"라는 뜻으로 취급한다 — `"".count("")`가 1이 아니라서(len+1)
    빈 문자열은 일반 치환 경로로 셀 수 없다.
    """
    if old == "":
        return f"{text}\n\n{new}" if text else new

    count = text.count(old)
    if count == 0:
        raise GuidePatchNotFound(f"가이드 본문에서 old 를 찾을 수 없다: {old!r}")
    if count > 1:
        raise GuidePatchAmbiguous(f"old 가 가이드 본문에 {count}번 나타난다: {old!r}")
    return text.replace(old, new, 1)


def apply_patches(text: str, patches: Sequence[tuple[str, str]]) -> str:
    """`patches`(old, new) 를 순서대로 하나씩 `apply_patch`로 적용한다.

    한 번의 REVISE(general) 피드백에 서로 다른 지시가 여러 개 섞여 있을 수 있어(메모리
    resume-revise-feedback-design) 여러 patch 를 받는다. 원자적이지 않다 — 중간에 하나가
    실패(`GuidePatchNotFound`/`GuidePatchAmbiguous`)하면 그 앞까지만 적용된 상태로 예외가
    전파된다. 둘 다 재시도 없이 사람에게 넘기는 실패라 부분 적용을 그대로 노출해도 된다는
    판단이다 — 실패한 patch 부터 사람이 다시 검토하면 된다.
    """
    for old, new in patches:
        text = apply_patch(text, old, new)
    return text
