import { describe, expect, it } from "vitest";
import {
  hasProblems,
  initialReview,
  reviewProblems,
  toContent,
  toSelection,
} from "@/features/onboarding/review-model";
import type { ExtractionDraft } from "@/lib/draft-api";

const EMPTY_ADDITIONAL = {
  military: null,
  veteran: null,
  disability: null,
  desired_salary: null,
  available_from: null,
  residence: null,
};

function draft(overrides: Partial<ExtractionDraft["content"]["profile"]> = {}): ExtractionDraft {
  return {
    id: "d1",
    user_id: "local",
    source: "resume",
    document_id: null,
    source_filename: "resume.pdf",
    redacted_identifiers: 1,
    created_at: "2026-09-30T00:00:00+00:00",
    content: {
      profile: {
        name: "김가상",
        phone: "",
        email: "kim@example.com",
        links: [],
        education: [{ school: "가상대", period: "2014 - 2018", status: "졸업", degree: "", note: "" }],
        skills: ["Python"],
        languages: [],
        additional: { ...EMPTY_ADDITIONAL, residence: "서울" },
        ...overrides,
      },
      experiences: [
        { kind: "company", name: "A사", role: "", period: "", url: null, skills: [], facts: [{ text: "a", skills: [] }], sections: [] },
        { kind: "project", name: "B", role: "", period: "", url: null, skills: [], facts: [], sections: [] },
      ],
    },
  };
}

describe("onboarding review model", () => {
  it("기본 선택은 값이 있는 인적사항 필드 + 모든 경험", () => {
    expect(toSelection(initialReview(draft()))).toEqual({
      profile_fields: ["name", "email", "education", "skills", "additional"],
      experience_indexes: [0, 1],
    });
  });

  it("버린 경험도 초안 내용에는 남아 인덱스가 초안 위치를 가리킨다", () => {
    const state = initialReview(draft());
    state.keep[0] = false;
    expect(toSelection(state).experience_indexes).toEqual([1]);
    expect(toContent(state).experiences.map((e) => e.name)).toEqual(["A사", "B"]);
  });

  it("고치지 않으면 초안 내용이 그대로 나온다 (불필요한 변경 없음)", () => {
    const d = draft();
    expect(toContent(initialReview(d))).toEqual(d.content);
  });

  it("인적사항을 고르는데 병합 결과에 이름이 없으면 막는다", () => {
    const state = initialReview(draft({ name: "" }));
    expect(reviewProblems(state, null).nameMissing).toBe(true);
    expect(reviewProblems(state, "기존이름").nameMissing).toBe(false);
    state.fields = { ...state.fields, email: false, education: false, skills: false, additional: false };
    expect(reviewProblems(state, null).nameMissing).toBe(false); // 인적사항을 안 고르면 이름도 필요 없다
  });

  it("아무것도 고르지 않았거나 경험 이름이 비면 문제", () => {
    const state = initialReview(draft());
    expect(hasProblems(reviewProblems(state, null))).toBe(false);
    state.experiences[1] = { ...state.experiences[1], name: " " };
    expect(reviewProblems(state, null).experiences[1]?.name).toBe(true);
    const none = initialReview(draft());
    none.fields = Object.fromEntries(Object.keys(none.fields).map((k) => [k, false])) as typeof none.fields;
    none.keep = [false, false];
    expect(reviewProblems(none, null).nothingSelected).toBe(true);
  });
});
