"""제출 클릭 분류(§A4 L2)·완료 감지(§A4 L5) 어휘 — 규칙(`submit_classifier.py`)과 분리한 데이터.

모든 항목은 정규화된 형태로 적는다: NFKC → casefold → 한국어는 글자·숫자만 남긴 "압축형"
(공백·구두점 제거), 영어는 영숫자 토큰. 어휘를 늘릴 때 규칙 코드는 건드리지 않는다.
"""

import re

# --- L2 위험 어휘 -------------------------------------------------------------
# 한국어: 압축형 부분 문자열. "지원" 하나로 지원하기·입사지원·간편지원·지원 완료를 모두 덮는다 —
# "지원 분야" 같은 탭도 위험으로 가지만, 위험 판정은 strict 창에서 실행될 뿐 막히지 않으므로 싸다.
RISKY_KO: tuple[str, ...] = (
    "제출",
    "지원",
    "최종",
    "완료",
    "확인",
    "등록",
    "접수",
    "신청",
    "전송",
    "보내기",
    "발송",
    "동의",
    "결제",
)

# 영어: 압축형 부분 문자열로 찾는다(띄어 쓴 "S u b m i t" 도 걸리게). 4글자 이상만 — 짧은 말은
# "bookmark" 안의 "ok" 처럼 엉뚱한 곳에 걸린다.
RISKY_EN_STEMS: tuple[str, ...] = (
    "submit",
    "submission",
    "apply",
    "send",
    "finish",
    "confirm",
    "complet",
    "finaliz",
    "finalis",
    "register",
    "enroll",
    "agree",
    "accept",
    "proceed",
    "checkout",
)

# 영어 짧은 말: 토큰이 정확히 같을 때만.
RISKY_EN_TOKENS: frozenset[str] = frozenset({"ok", "okay", "yes", "done", "pay"})

# dialog 안의 긍정 응답 — 라벨 전체(압축형)가 이것과 같으면 DIALOG_CONFIRM.
DIALOG_AFFIRMATIVE: frozenset[str] = frozenset(
    {"확인", "예", "네", "ok", "okay", "yes", "y", "confirm", "동의", "계속", "continue", "진행"}
)

# --- L2 안전 어휘 -------------------------------------------------------------
# 위험 어휘가 하나도 없고, 아래 중 하나가 있을 때만 버튼류를 Safe 로 본다. 목록에 없는 라벨은
# 전부 Risky(판단 불가) — 닫힌 쪽으로 실패.
SAFE_KO: tuple[str, ...] = (
    "다음",
    "이전",
    "뒤로",
    "저장",
    "계속",
    "파일선택",
    "파일첨부",
    "첨부",
    "업로드",
    "찾아보기",
    "추가",
    "삭제",
    "닫기",
    "취소",
    "수정",
    "편집",
    "더보기",
    "펼치기",
    "접기",
    "검색",
    "선택",
)

SAFE_EN_TOKENS: frozenset[str] = frozenset(
    {
        "next",
        "previous",
        "prev",
        "back",
        "continue",
        "save",
        "draft",
        "upload",
        "browse",
        "choose",
        "select",
        "attach",
        "add",
        "remove",
        "delete",
        "edit",
        "close",
        "cancel",
        "more",
        "expand",
        "collapse",
        "search",
        "show",
        "hide",
    }
)

# --- D17 단계 이동 어휘 -------------------------------------------------------
# 제출 컨트롤(type=submit)이라도 라벨 **전체**(압축형)가 이 중 하나면 단계 이동(STEP)이다 — 부분
# 일치가 아니다("다음에 제출"·"Continue to review" 는 STEP 이 아니다). "저장"·"임시저장"·"이전" 은
# 단계를 넘기지 않으므로 넣지 않는다(그런 submit 은 지금처럼 strict). 영어는 영숫자만 남긴 형태.
STEP_PHRASES: frozenset[str] = frozenset(
    {
        "다음",
        "다음단계",
        "다음단계로",
        "다음으로",
        "다음단계로이동",
        "다음페이지",
        "계속",
        "계속하기",
        "저장후계속",
        "저장하고계속",
        "저장후다음",
        "저장하고다음",
        "저장후다음단계",
        "next",
        "nextstep",
        "nextpage",
        "continue",
        "continuetonextstep",
        "saveandcontinue",
        "savecontinue",
        "saveandnext",
        "savenext",
    }
)

# 마지막 단계 신호(D17) — 같은 페이지에 이게 있으면 단계 어휘 버튼도 Risky(LAST_STEP).
# 한국어: 압축형 부분 문자열. "최종" 하나는 "최종학력" 에 걸려 쓰지 않는다.
LAST_STEP_KO: tuple[str, ...] = (
    "제출전",
    "제출하기전",
    "제출하시기전",
    "최종제출",
    "최종확인",
    "최종검토",
    "제출후에는",
    "제출후수정",
    "제출이후",
    "지원후에는",
    "지원후수정",
    "지원서를검토",
    "지원내용확인",
    "입력내용확인",
    "위내용이사실",
    "기재한내용이사실",
    "기재된내용이사실",
    "입력한내용이사실",
    "입력하신내용이사실",
)

# 영어: 공백을 하나로 접은 casefold 줄에 적용.
LAST_STEP_EN = re.compile(
    r"\breview (your|the) application\b"
    r"|\bbefore (you )?submit"
    r"|\bonce (you('ve| have) )?submit"
    r"|\bafter submi(tting|ssion),? (you )?(can(no|')t|will not|won't)"
    r"|\bfinal (review|step|confirmation)\b"
    r"|\breview (and|&) submit\b"
    r"|\bby submitting\b"
    r"|\bi (hereby )?(certify|declare|attest)\b"
    r"|\bconfirm (that )?(the|all|this) information\b"
)

# 글자 진행 표시 — (현재, 전체). 줄마다(접고 공백을 하나로) 찾는다. "3/3" 같은 맨 숫자는 날짜와
# 헷갈려 "단계·step" 이 붙은 것만 본다.
PROGRESS_TEXT: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:step|단계) ?(?P<cur>\d{1,2}) ?(?:/|of|중) ?(?P<total>\d{1,2})"),
    re.compile(r"(?P<cur>\d{1,2}) ?(?:/|of) ?(?P<total>\d{1,2}) ?(?:steps?\b|단계)"),
    re.compile(r"(?P<cur>\d{1,2}) ?단계 ?/ ?(?P<total>\d{1,2}) ?단계"),
    re.compile(r"(?:총|전체) ?(?P<total>\d{1,2}) ?단계 ?중 ?(?P<cur>\d{1,2}) ?단계"),
)

# 클릭해도 제출이 일어날 수 없는 입력 요소 — 라벨과 무관하게 Safe(위험 어휘가 없을 때).
INERT_INPUT_TYPES: frozenset[str] = frozenset(
    {
        "text",
        "email",
        "tel",
        "number",
        "password",
        "search",
        "url",
        "date",
        "datetime-local",
        "month",
        "week",
        "time",
        "checkbox",
        "radio",
        "file",
        "range",
        "color",
    }
)
INERT_TAGS: frozenset[str] = frozenset({"textarea", "select", "option"})
INERT_ROLES: frozenset[str] = frozenset(
    {
        "checkbox",
        "radio",
        "switch",
        "tab",
        "option",
        "textbox",
        "combobox",
        "searchbox",
        "slider",
        "spinbutton",
    }
)

# HTML: 이 type 의 input 은 폼을 제출한다.
SUBMIT_INPUT_TYPES: frozenset[str] = frozenset({"submit", "image"})
# HTML: button 은 type 이 이 둘일 때만 제출 버튼이 아니다(없거나 알 수 없는 값이면 submit).
NON_SUBMIT_BUTTON_TYPES: frozenset[str] = frozenset({"button", "reset"})

# --- L5 완료 어휘 -------------------------------------------------------------
# 폼 안내문("지원이 완료되면 메일로 알려드립니다")·제출 버튼 라벨("지원 완료")에 걸리지 않게
# 한국어는 과거형(되었/됐)이나 감사 인사까지 요구한다. 압축형에 적용한다.
COMPLETION_KO = re.compile(
    r"(지원|지원서|제출|접수|입사지원)[이가은는]?(정상적으로|성공적으로)?"
    r"(완료|제출|접수)?(되었|됐)"
    r"|지원해주셔서감사"
    r"|지원해주셔서고맙"
)

# 영어는 casefold 원문(공백 하나로 접고 곡선 따옴표를 ' 로 바꾼 것)에 적용한다.
COMPLETION_EN = re.compile(
    r"\bapplication (has been |was |is )?(successfully )?(received|submitted|sent|complete)\b"
    r"|\bwe('ve| have) received your application"
    r"|\bthank(s| you) for (applying|your application|submitting)"
    r"|\bsuccessfully (submitted|applied)\b"
    r"|\bsubmission (received|complete)\b"
    r"|\byou('ve| have) (successfully )?applied\b"
)

# URL 경로·쿼리 토큰(호스트 제외). 공고 슬러그에 흔한 말은 뺐다 — "customer-success-manager"·
# "applied-ai-engineer"·"/profile/complete" 가 걸리면 그 페이지의 모든 클릭이 INCIDENT 가 된다.
COMPLETION_URL_TOKENS: frozenset[str] = frozenset(
    {"thankyou", "thanks", "thank", "submitted", "completed", "confirmation"}
)
