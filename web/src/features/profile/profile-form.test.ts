import { describe, expect, it } from "vitest";
import { additionalProgress, toBody, toDraft } from "@/features/profile/profile-form";
import type { Profile } from "@/lib/profile-api";

const saved: Profile = {
  user_id: "local",
  name: "홍길동",
  phone: "010-0000-0000",
  email: "gildong@example.com",
  links: [{ label: "GitHub", url: "https://github.com/gildong" }],
  education: [{ school: "가상대학교", period: "2018.03 - 2024.02", status: "졸업", degree: "", note: "" }],
  skills: ["Python", "React"],
  languages: [{ name: "영어", level: "TOEIC 900" }],
  additional: {
    military: { status: "completed", branch: "육군", rank: "병장", period: "", note: "" },
    veteran: false,
    disability: null,
    desired_salary: "회사 내규에 따름",
    available_from: null,
    residence: null,
  },
};

describe("profile-form", () => {
  it("저장된 프로필은 양식을 거쳐도 그대로 돌아온다", () => {
    const { user_id: _, ...body } = saved;
    expect(toBody(toDraft(saved))).toEqual(body);
  });

  it("프로필이 없으면 추가 정보가 전부 null(지원 중 질문) 인 빈 본문이 된다", () => {
    const body = toBody(toDraft(null));
    expect(body.name).toBe("");
    expect(Object.values(body.additional).every((v) => v === null)).toBe(true);
  });

  it("추가 정보의 빈 칸·공백만 있는 칸은 null 로, false 는 false 로 보낸다 (D10)", () => {
    const draft = toDraft(saved);
    draft.additional = {
      ...draft.additional,
      desiredSalary: "   ",
      residence: " 서울 ",
      veteran: "no",
      disability: "",
    };
    const a = toBody(draft).additional;
    expect(a.desired_salary).toBeNull();
    expect(a.residence).toBe("서울");
    expect(a.veteran).toBe(false);
    expect(a.disability).toBeNull();
  });

  it("병역 사항을 비워두면 세부 칸이 있어도 military 는 null 이다", () => {
    const draft = toDraft(saved);
    draft.additional = { ...draft.additional, militaryStatus: "" };
    expect(toBody(draft).additional.military).toBeNull();
  });

  it("스킬은 쉼표·줄바꿈으로 나누고 빈 값·중복을 버린다", () => {
    const draft = { ...toDraft(saved), skillsText: "Python, React,\n Python ,, Go" };
    expect(toBody(draft).skills).toEqual(["Python", "React", "Go"]);
  });

  it("모든 칸이 빈 목록 행은 버린다", () => {
    const draft = { ...toDraft(saved), links: [...saved.links, { label: " ", url: "" }] };
    expect(toBody(draft).links).toEqual(saved.links);
  });

  it("추가 정보 진행도는 null 이 아닌 항목 수다", () => {
    expect(additionalProgress(toDraft(saved).additional)).toEqual({ filled: 3, total: 6 });
    expect(additionalProgress(toDraft(null).additional)).toEqual({ filled: 0, total: 6 });
  });
});
