// contracts/profile.py · api/schemas.py(ProfileBody) 를 그대로 옮긴 타입. 백엔드가 진짜 계약이다 —
// 필드를 늘릴 땐 여기와 그쪽을 같이 고친다.
import { isNotFound, request } from "@/lib/api";

export const MILITARY_STATUSES = [
  "completed",
  "serving",
  "not_completed",
  "exempt",
  "not_applicable",
] as const;
export type MilitaryStatus = (typeof MILITARY_STATUSES)[number];

// 목록 행 편집기(ListEditor)가 문자열 레코드로 다루게 interface 가 아니라 type 이다(암묵 인덱스 시그니처).
export type EducationEntry = {
  school: string;
  period: string;
  status: string;
  degree: string;
  note: string;
};

export type LanguageEntry = {
  name: string;
  level: string;
};

export type ProfileLink = {
  label: string;
  url: string;
};

export interface MilitaryService {
  status: MilitaryStatus;
  branch: string;
  rank: string;
  period: string;
  note: string;
}

// null = 아직 입력 안 함 → 지원 중에 묻는다 (D10). false/빈 값("해당 없음")과 다르다.
export interface AdditionalInfo {
  military: MilitaryService | null;
  veteran: boolean | null;
  disability: boolean | null;
  desired_salary: string | null;
  available_from: string | null;
  residence: string | null;
}

export interface Profile {
  user_id: string;
  name: string;
  phone: string;
  email: string;
  links: ProfileLink[];
  education: EducationEntry[];
  skills: string[];
  languages: LanguageEntry[];
  additional: AdditionalInfo;
}

export type ProfileBody = Omit<Profile, "user_id">;

export const profileApi = {
  // 아직 저장한 적 없으면 null — 빈 양식으로 시작한다.
  get: () =>
    request<Profile>("/api/profile").catch((e: unknown) => {
      if (isNotFound(e)) return null;
      throw e;
    }),

  save: (body: ProfileBody) =>
    request<Profile>("/api/profile", { method: "PUT", body: JSON.stringify(body) }),
};
