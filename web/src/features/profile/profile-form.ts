// 서버 Profile ↔ 화면 양식 변환. 양식은 입력 칸 그대로(문자열)를 들고, 저장할 때만 서버 모양으로 바꾼다 —
// 특히 추가 정보의 빈 칸은 null("아직 모름", D10)로 보내야 실행 중 질문 대상이 된다.
import type {
  AdditionalInfo,
  MilitaryStatus,
  Profile,
  ProfileBody,
} from "@/lib/profile-api";

export type TriState = "" | "yes" | "no";

export interface AdditionalDraft {
  militaryStatus: MilitaryStatus | "";
  militaryBranch: string;
  militaryRank: string;
  militaryPeriod: string;
  militaryNote: string;
  veteran: TriState;
  disability: TriState;
  desiredSalary: string;
  availableFrom: string;
  residence: string;
}

export interface ProfileDraft extends Omit<ProfileBody, "skills" | "additional"> {
  skillsText: string;
  additional: AdditionalDraft;
}

function toTri(value: boolean | null): TriState {
  if (value === null) return "";
  return value ? "yes" : "no";
}

function fromTri(value: TriState): boolean | null {
  if (value === "") return null;
  return value === "yes";
}

function orNull(text: string): string | null {
  const trimmed = text.trim();
  return trimmed === "" ? null : trimmed;
}

export function toDraft(profile: Profile | null): ProfileDraft {
  const a = profile?.additional;
  const m = a?.military ?? null;
  return {
    name: profile?.name ?? "",
    phone: profile?.phone ?? "",
    email: profile?.email ?? "",
    links: profile?.links ?? [],
    education: profile?.education ?? [],
    languages: profile?.languages ?? [],
    skillsText: (profile?.skills ?? []).join(", "),
    additional: {
      militaryStatus: m?.status ?? "",
      militaryBranch: m?.branch ?? "",
      militaryRank: m?.rank ?? "",
      militaryPeriod: m?.period ?? "",
      militaryNote: m?.note ?? "",
      veteran: toTri(a?.veteran ?? null),
      disability: toTri(a?.disability ?? null),
      desiredSalary: a?.desired_salary ?? "",
      availableFrom: a?.available_from ?? "",
      residence: a?.residence ?? "",
    },
  };
}

export function parseSkills(text: string): string[] {
  const items = text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
  return [...new Set(items)];
}

function toAdditional(d: AdditionalDraft): AdditionalInfo {
  return {
    // 병역 사항을 고르지 않았으면 세부 칸이 있어도 "아직 모름"이다.
    military:
      d.militaryStatus === ""
        ? null
        : {
            status: d.militaryStatus,
            branch: d.militaryBranch.trim(),
            rank: d.militaryRank.trim(),
            period: d.militaryPeriod.trim(),
            note: d.militaryNote.trim(),
          },
    veteran: fromTri(d.veteran),
    disability: fromTri(d.disability),
    desired_salary: orNull(d.desiredSalary),
    available_from: orNull(d.availableFrom),
    residence: orNull(d.residence),
  };
}

export function toBody(d: ProfileDraft): ProfileBody {
  return {
    name: d.name.trim(),
    phone: d.phone.trim(),
    email: d.email.trim(),
    links: d.links.filter((l) => l.label.trim() || l.url.trim()),
    education: d.education.filter((e) => e.school.trim() || e.period.trim()),
    skills: parseSkills(d.skillsText),
    languages: d.languages.filter((l) => l.name.trim() || l.level.trim()),
    additional: toAdditional(d.additional),
  };
}

// 추가 정보 중 채운 항목 수 — 접힌 섹션 머리에 보여 준다.
export function additionalProgress(d: AdditionalDraft): { filled: number; total: number } {
  const values = Object.values(toAdditional(d));
  return { filled: values.filter((v) => v !== null).length, total: values.length };
}
