"""공고 신원 — 정규화·canonical_key·검색용 텍스트 추출. 전부 순수 함수.

canonical_key는 중복지원 방어선이다. 같은 자리가 여러 플랫폼에 각각 올라와도
키가 같아야 두 번 지원하지 않는다. 정규화 강도는 의도적으로 "애매하면 합치는
쪽"으로 기울인다 — 중복지원은 사람이 수습해야 하는 사고고, 누락은 다음 수집 때
또 볼 기회가 있어서 무게가 다르다 (구 프로젝트 normalize.py 의 판단을 그대로 가져옴).
"""

import hashlib
import re

from auto_apply.contracts.job import JobPosting

# 회사명에서 걷어낼 법인격 표기
_LEGAL = re.compile(
    r"\(주\)|\(유\)|\(재\)|\(사\)|주식회사|유한회사|재단법인|사단법인|"
    r"\bco\.?,?\s*ltd\.?|\binc\.?|\bcorp\.?|\bllc\b",
    re.IGNORECASE,
)

# 제목에서 걷어낼 상투어. 직무를 가르지 못하는 단어만 넣는다.
# '웹', '앱', '프론트', '백엔드' 같은 것은 절대 넣지 않는다 — 그건 합치면 안 되는
# 진짜 직무 구분이다.
_BOILERPLATE = re.compile(
    r"20\d{2}\s*년?도?|\d+분기|상·?하반기|상반기|하반기|수시|공개|공채|채용|모집|"
    r"신입\s*및\s*경력|신입/경력|경력직|신입직?|인턴십?|정규직|계약직|"
    r"[○◯●◆■▶★☆]|\bnew\b|\bhot\b|\d+차",
    re.IGNORECASE,
)

_BRACKET = re.compile(r"[\[\](){}<>【】〔〕«»]")
_PUNCT = re.compile(r"[·・,./\\|~\-–—_:;!?\"'`]+")
_SPACE = re.compile(r"\s+")

_PAREN_GROUP = re.compile(r"[(（\[［][^)）\]］]*[)）\]］]")
_PAREN_LATIN = re.compile(r"[(（\[［][\sA-Za-z0-9&.,'’\-]*[)）\]］]")


def normalize_company(name: str) -> str:
    """'(주) 라스트스프링(LASTSPRING)' → '라스트스프링'.

    법인격도 영문 병기도 회사를 가르지 않으므로 통째로 지운다.
    """
    s = _LEGAL.sub(" ", name or "")
    s = _PAREN_GROUP.sub(" ", s)
    s = _BRACKET.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return _SPACE.sub("", s).strip().lower()


def normalize_title(raw: str) -> str:
    """'[2026 상반기] 백엔드 개발자 신입 채용' → '백엔드개발자'.

    괄호 안이 라틴문자뿐이면 영문 병기라 지우고, 한글이 있으면 직무 구분일 수
    있으므로 괄호만 벗기고 내용은 남긴다.
    """
    s = _PAREN_LATIN.sub(" ", raw or "")
    s = _BRACKET.sub(" ", s)
    s = _BOILERPLATE.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return _SPACE.sub("", s).strip().lower()


def canonical_key(company_name: str, job_title: str) -> str:
    """지원 원장의 유일키. 회사+직무가 같으면 플랫폼이 달라도 같은 키가 나온다."""
    c, t = normalize_company(company_name), normalize_title(job_title)
    if not c and not t:
        return ""
    return hashlib.sha256(f"{c}|{t}".encode()).hexdigest()[:20]


def content_hash(job: JobPosting) -> str:
    """본문이 바뀌었는지 감지하는 지문. 재수집 시 변경분만 재판정하는 데 쓴다."""
    blob = f"{job.title}|{job.company}|{job.deadline}|{job.description[:2000]}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def searchable_text(job: JobPosting) -> str:
    """필터가 훑는 텍스트 뭉치."""
    parts = [
        job.title,
        job.category,
        job.company,
        job.location,
        job.employment_type,
        job.experience_req,
        job.education_req,
        job.description,
    ]
    return " ".join(p for p in parts if p).lower()


def headline_text(job: JobPosting) -> str:
    """공고가 스스로 무슨 일인지 밝힌 자리만 모은 것 — 제목과 플랫폼 직무그룹.

    본문에는 복리후생·채용절차·회사소개가 섞여 있어 직무와 무관한 단어가 깔린다.
    제목과 직무그룹에는 그 잡음이 없다 — 트랙 판정이 여기를 더 무겁게 본다
    (domain/job_screening.py 참고).
    """
    return " ".join(p for p in (job.title, job.category) if p).lower()
