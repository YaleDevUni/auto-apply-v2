import { useTranslation } from "react-i18next";
import { safeHttpUrl } from "@/lib/safe-url";

interface SummaryFact {
  text: string;
}

// 저장된 경험과 초안 경험이 공통으로 가진 필드만 본다.
export interface SummaryData {
  name: string;
  role: string;
  period: string;
  url: string | null;
  skills: string[];
  facts: SummaryFact[];
  sections: { title: string; period: string; facts: SummaryFact[] }[];
}

function FactList({ facts }: { facts: SummaryFact[] }) {
  if (facts.length === 0) return null;
  return (
    <ul className="list-disc space-y-0.5 pl-5">
      {facts.map((f, i) => (
        <li key={i}>{f.text}</li>
      ))}
    </ul>
  );
}

export function ExperienceSummary({ data, extra }: { data: SummaryData; extra?: string }) {
  const { t } = useTranslation();
  const meta = [data.role, data.period, extra].filter(Boolean).join(" · ");
  const href = safeHttpUrl(data.url);
  return (
    <div className="flex min-w-0 flex-col gap-2 text-sm">
      <div>
        <p className="font-semibold">{data.name || t("experiences.unnamed")}</p>
        {meta && <p className="text-muted-foreground">{meta}</p>}
        {href ? (
          <a href={href} target="_blank" rel="noreferrer" className="text-primary break-all underline-offset-4 hover:underline">
            {data.url}
          </a>
        ) : (
          data.url && <p className="text-muted-foreground break-all">{data.url}</p>
        )}
      </div>
      {data.skills.length > 0 && (
        <p className="text-muted-foreground text-xs">{data.skills.join(" · ")}</p>
      )}
      <FactList facts={data.facts} />
      {data.sections.map((s, i) => (
        <div key={i} className="flex flex-col gap-1">
          <p className="font-medium">
            {s.title}
            {s.period && <span className="text-muted-foreground font-normal"> · {s.period}</span>}
          </p>
          <FactList facts={s.facts} />
        </div>
      ))}
    </div>
  );
}
