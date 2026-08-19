<!--
config/resume_guide.{platform}.md — 플랫폼별 이력서 작성 가이드 (ARCHITECTURE.md §2.2, §2.3).

REVISE(수정요청) → scope=GENERAL 을 사람이 승인하면 이 파일에 규칙이 patch(치환 쌍)로 쌓인다
(domain/guide_patch.py). 사람이 직접 줄을 추가/수정해도 된다 — SimpleResumeGenerator 가
이력서를 생성할 때마다 다시 읽는다(캐시 없음).

파일명의 {platform} 은 JobRef.platform 값과 정확히 일치해야 한다 (예: wanted, saramin,
jasoseol). 플랫폼마다 이력서 포맷·관례가 달라 가이드도 갈릴 수 있어서 파일을 나눴다 — 지금은
원티드(resume_guide.wanted.md)만 실제로 쓰지만, 새 플랫폼은 이 이름 규칙대로 파일을 하나 더
두는 것만으로 확장된다(코드 변경 불필요).

이 파일들은 사람이 쓴 커스텀 프롬프트라 .gitignore 로 뺐다 — 이 example 파일만 형식 공유용으로
git에 남긴다 (config/facts.example.yaml, config/profile.example.yaml 과 같은 패턴).
아래는 실제로 반영됐던 규칙의 예시(placeholder)다. 실제 내용으로 교체할 것.
-->

- 전체 문서의 말투는 정중체(합니다/했습니다 또는 이에 준하는 정중한 표현)로 작성한다.
- 간단소개(자기소개) 섹션은 한 줄 자기소개로 작성한다. 지원 공고가 요구하는 방향에 맞춰
  "~한 개발자입니다"와 같은 형태로, 여러 줄을 나열하지 말고 정중한 한 문장으로 제시한다.
