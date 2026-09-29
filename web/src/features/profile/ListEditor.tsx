import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Field, Section } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export interface ListColumn<T> {
  key: keyof T & string;
  label: string;
  placeholder?: string;
}

// 링크·학력·언어처럼 "문자열 칸 몇 개짜리 행"의 반복 목록.
export function ListEditor<T extends Record<string, string>>({
  id,
  title,
  columns,
  rows,
  empty,
  onChange,
}: {
  id: string;
  title: string;
  columns: ListColumn<T>[];
  rows: T[];
  empty: T;
  onChange: (rows: T[]) => void;
}) {
  const { t } = useTranslation();
  const update = (index: number, key: keyof T, value: string) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, [key]: value } : row)));

  return (
    <Section title={title}>
      {rows.map((row, index) => (
        <div key={index} className="flex items-end gap-2">
          <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {columns.map((col) => {
              const inputId = `${id}-${index}-${col.key}`;
              return (
                <Field key={col.key} label={col.label} htmlFor={inputId}>
                  <Input
                    id={inputId}
                    value={row[col.key]}
                    placeholder={col.placeholder}
                    onChange={(e) => update(index, col.key, e.target.value)}
                  />
                </Field>
              );
            })}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`${title} ${index + 1} ${t("common.remove")}`}
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <div>
        <Button type="button" variant="outline" size="sm" onClick={() => onChange([...rows, { ...empty }])}>
          <Plus />
          {t("common.add")}
        </Button>
      </div>
    </Section>
  );
}
