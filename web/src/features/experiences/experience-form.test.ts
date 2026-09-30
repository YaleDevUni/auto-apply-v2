import { describe, expect, it } from "vitest";
import {
  emptyExperience,
  emptySection,
  factCount,
  fromExperience,
  fromExtracted,
  toExperienceBody,
  toExtracted,
  validateExperience,
} from "@/features/experiences/experience-form";
import type { Experience } from "@/lib/knowledge-api";

const saved: Experience = {
  id: "exp_1",
  user_id: "local",
  kind: "company",
  name: "가상회사",
  role: "백엔드",
  period: "2022.01 - 2024.02",
  url: null,
  skills: ["Python"],
  document_ids: ["doc_1"],
  facts: [{ id: "f_1", text: "결제 API 응답시간 40% 단축", skills: [] }],
  sections: [{ key: "s2", title: "정산 시스템", period: "", facts: [{ id: "f_2", text: "배치 전환", skills: ["Kotlin"] }] }],
};

describe("experience form", () => {
  it("저장된 경험을 그대로 되돌리면 fact id·섹션 key 가 유지된다 (이력서 근거가 끊기지 않게)", () => {
    const body = toExperienceBody(fromExperience(saved));
    expect(body.facts).toEqual([{ id: "f_1", text: "결제 API 응답시간 40% 단축", skills: [] }]);
    expect(body.sections[0]).toMatchObject({ key: "s2", facts: [{ id: "f_2", skills: ["Kotlin"] }] });
    expect(body.document_ids).toEqual(["doc_1"]);
    expect(body.url).toBeNull();
  });

  it("새 fact 는 id null(서버 발급), 새 섹션은 기존 key 와 겹치지 않는 번호를 받는다", () => {
    const form = fromExperience(saved);
    form.facts.push({ id: null, text: " 신규 서술 ", skillsText: "Go, Go, Rust" });
    form.sections.push({ ...emptySection(), title: "A" }, { ...emptySection(), title: "B" });
    form.sections[1].facts[0].text = "a";
    form.sections[2].facts[0].text = "b";
    const body = toExperienceBody(form);
    expect(body.facts[1]).toEqual({ id: null, text: "신규 서술", skills: ["Go", "Rust"] });
    expect(body.sections.map((s) => s.key)).toEqual(["s2", "s1", "s3"]);
  });

  it("빈 서술 행과 완전히 빈 섹션은 버린다", () => {
    const form = { ...emptyExperience(), name: "x", sections: [emptySection()] };
    const body = toExperienceBody(form);
    expect(body.facts).toEqual([]);
    expect(body.sections).toEqual([]);
    expect(validateExperience(form)).toBeNull();
  });

  it("이름이 없거나, 서술이 있는 섹션의 제목이 없으면 검증 실패", () => {
    const form = { ...emptyExperience(), sections: [{ ...emptySection(), facts: [{ id: null, text: "t", skillsText: "" }] }] };
    expect(validateExperience(form)).toEqual({ name: true, sectionTitles: [0] });
  });

  it("초안 경험 ↔ 양식 왕복은 초안 모양(id·key 없음)을 지킨다", () => {
    const extracted = {
      kind: "project" as const,
      name: "사이드",
      role: "",
      period: "",
      url: "https://example.com",
      skills: ["TS"],
      facts: [{ text: "만듦", skills: [] }],
      sections: [{ title: "하위", period: "2023", facts: [{ text: "x", skills: ["Go"] }] }],
    };
    expect(toExtracted(fromExtracted(extracted))).toEqual(extracted);
  });

  it("서술 개수는 섹션 안까지 센다", () => {
    expect(factCount(saved)).toBe(2);
  });
});
