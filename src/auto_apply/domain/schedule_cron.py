"""시/분 정수 → cron 표현식. 순수 함수.

채팅으로 스케줄 시각을 바꿀 때(§ apply-schedule) LLM은 hour/minute 정수만 뽑고, cron 문법
자체는 여기 코드가 조립한다 — Recipe/가이드 patch와 같은 "AI는 생성만, 조합은 코드" 철학의
연장이다. LLM이 cron 표현식 문자열 자체를 만들게 하면 실패·오해석 위험이 있어서 피한다.
"""


def build_cron(hour: int, minute: int) -> str:
    if not 0 <= hour <= 23:
        raise ValueError(f"hour 는 0~23 이어야 합니다: {hour}")
    if not 0 <= minute <= 59:
        raise ValueError(f"minute 는 0~59 이어야 합니다: {minute}")
    return f"{minute} {hour} * * *"
