// 경험 편집 양식 모델. 저장된 경험(Experience)과 온보딩 초안의 경험(ExtractedExperience)을 같은 편집기로 고치려고
// 한 모양으로 받는다. 양식은 입력 칸 그대로(문자열)를 들고 저장할 때만 서버 모양으로 바꾼다.
import type { ExtractedExperience, ExtractedFact } from "@/lib/draft-api";
import type {
  Experience,
  ExperienceBody,
  ExperienceFact,
  ExperienceFactBody,
  ExperienceKind,
} from "@/lib/knowledge-api";
import { parseSkills } from "@/features/profile/profile-form";

export interface FactForm {
  // 저장된 fact 의 id — 이력서 서술이 근거로 인용한다(ground_check). 새 fact 는 null(서버 발급).
  id: string | null;
  text: string;
  skillsText: string;
}

export interface SectionForm {
  key: string | null; // 새 섹션은 null — 저장할 때 경험 안에서 겹치지 않는 key 를 붙인다
  title: string;
  period: string;
  facts: FactForm[];
}

export interface ExperienceForm {
  kind: ExperienceKind;
  name: string;
  role: string;
  period: string;
  url: string;
  skillsText: string;
  documentIds: string[];
  facts: FactForm[];
  sections: SectionForm[];
}

export interface ExperienceErrors {
  name: boolean;
  sectionTitles: number[]; // 서술이 있는데 제목이 빈 섹션 위치
}

export const emptyFact = (): FactForm => ({ id: null, text: "", skillsText: "" });
export const emptySection = (): SectionForm => ({ key: null, title: "", period: "", facts: [emptyFact()] });

export function emptyExperience(kind: ExperienceKind = "company"): ExperienceForm {
  return { kind, name: "", role: "", period: "", url: "", skillsText: "", documentIds: [], facts: [emptyFact()], sections: [] };
}

const factForm = (f: ExperienceFact | ExtractedFact): FactForm => ({
  id: "id" in f ? f.id : null,
  text: f.text,
  skillsText: f.skills.join(", "),
});

export function fromExperience(e: Experience): ExperienceForm {
  return {
    kind: e.kind,
    name: e.name,
    role: e.role,
    period: e.period,
    url: e.url ?? "",
    skillsText: e.skills.join(", "),
    documentIds: e.document_ids,
    facts: e.facts.map(factForm),
    sections: e.sections.map((s) => ({ key: s.key, title: s.title, period: s.period, facts: s.facts.map(factForm) })),
  };
}

export function fromExtracted(e: ExtractedExperience): ExperienceForm {
  return {
    kind: e.kind,
    name: e.name,
    role: e.role,
    period: e.period,
    url: e.url ?? "",
    skillsText: e.skills.join(", "),
    documentIds: [],
    facts: e.facts.map(factForm),
    sections: e.sections.map((s) => ({ key: null, title: s.title, period: s.period, facts: s.facts.map(factForm) })),
  };
}

// 빈 서술 칸은 "안 씀"이다 — 추가 버튼으로 만든 빈 행이 저장을 막지 않게 버린다.
const keptFacts = (facts: FactForm[]) => facts.filter((f) => f.text.trim() !== "");
const isBlankSection = (s: SectionForm) => s.title.trim() === "" && keptFacts(s.facts).length === 0;

export function validateExperience(f: ExperienceForm): ExperienceErrors | null {
  const errors: ExperienceErrors = {
    name: f.name.trim() === "",
    sectionTitles: f.sections.flatMap((s, i) => (!isBlankSection(s) && s.title.trim() === "" ? [i] : [])),
  };
  return errors.name || errors.sectionTitles.length > 0 ? errors : null;
}

function factBody(f: FactForm): ExperienceFactBody {
  return { id: f.id, text: f.text.trim(), skills: parseSkills(f.skillsText) };
}

// 섹션 key 는 이력서 블록 id `{experience.id}:{key}` 의 뒷부분이라 기존 key 는 그대로 두고, 새 섹션만 빈 번호를 받는다.
function assignKeys(sections: SectionForm[]): string[] {
  const used = new Set(sections.flatMap((s) => (s.key === null ? [] : [s.key])));
  let n = 1;
  return sections.map((s) => {
    if (s.key !== null) return s.key;
    while (used.has(`s${n}`)) n += 1;
    const key = `s${n}`;
    used.add(key);
    return key;
  });
}

export function toExperienceBody(f: ExperienceForm): ExperienceBody {
  const sections = f.sections.filter((s) => !isBlankSection(s));
  const keys = assignKeys(sections);
  return {
    kind: f.kind,
    name: f.name.trim(),
    role: f.role.trim(),
    period: f.period.trim(),
    url: f.url.trim() === "" ? null : f.url.trim(),
    skills: parseSkills(f.skillsText),
    document_ids: f.documentIds,
    facts: keptFacts(f.facts).map(factBody),
    sections: sections.map((s, i) => ({
      key: keys[i],
      title: s.title.trim(),
      period: s.period.trim(),
      facts: keptFacts(s.facts).map(factBody),
    })),
  };
}

export function toExtracted(f: ExperienceForm): ExtractedExperience {
  const body = toExperienceBody(f);
  const fact = ({ text, skills }: ExperienceFactBody) => ({ text, skills });
  return {
    kind: body.kind,
    name: body.name,
    role: body.role,
    period: body.period,
    url: body.url,
    skills: body.skills,
    facts: body.facts.map(fact),
    sections: body.sections.map((s) => ({ title: s.title, period: s.period, facts: s.facts.map(fact) })),
  };
}

export function factCount(e: { facts: unknown[]; sections: { facts: unknown[] }[] }): number {
  return e.facts.length + e.sections.reduce((n, s) => n + s.facts.length, 0);
}
