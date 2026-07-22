import type { ReactElement } from "react";
import { ChipInput } from "@/shared/components";
import { Input } from "@/shared/components/ui";
import { formatCurrency } from "@/shared/lib";
import type { OnboardingSuggestionValue } from "@/shared/api";
import { displayValue, fieldMeta, type FieldKind } from "./constants";

function toStringList(value: OnboardingSuggestionValue): string[] {
  return Array.isArray(value) ? value : [];
}

/**
 * 후보값을 읽기 전용으로 표시(칩/텍스트/통화). 편집 종류는 필드 메타로 룩업(§4.5-2).
 * codeValued 필드(업무구분/카테고리)는 canonical 코드를 한국어로 **표시만** 매핑한다
 * (저장/전송 값 불변, ko 단일 번들 §8). 편집기는 raw 코드를 그대로 다룬다.
 */
export function SuggestionValueDisplay({
  field,
  value
}: {
  field: string;
  value: OnboardingSuggestionValue;
}) {
  const kind = fieldMeta(field).kind;
  if (kind === "chips") {
    const items = toStringList(value);
    if (items.length === 0) {
      return <span className="text-xs text-[var(--color-muted)]">값 없음</span>;
    }
    return (
      <ul className="flex flex-wrap gap-1" aria-label={`${fieldMeta(field).label} 값`}>
        {items.map((item) => (
          <li
            key={item}
            className="rounded-full bg-[var(--color-secondary)] px-2 py-0.5 text-xs text-[var(--color-secondary-foreground)]"
          >
            {displayValue(field, item)}
          </li>
        ))}
      </ul>
    );
  }
  if (kind === "number") {
    return (
      <span className="text-sm font-medium tabular-nums">
        {typeof value === "number" ? formatCurrency(value) : "-"}
      </span>
    );
  }
  return (
    <span className="text-sm font-medium">
      {typeof value === "string" ? displayValue(field, value) : "-"}
    </span>
  );
}

export interface SuggestionValueEditorProps {
  field: string;
  value: OnboardingSuggestionValue;
  onChange: (next: OnboardingSuggestionValue) => void;
}

/**
 * 후보값 편집기 — 필드 종류(text/chips/number)로 입력 위젯을 룩업 디스패치한다(§4.5-2).
 * chips 는 기존 `ChipInput` 을 재사용(§4.6, 복붙 금지).
 */
export function SuggestionValueEditor({ field, value, onChange }: SuggestionValueEditorProps) {
  const meta = fieldMeta(field);
  const editors: Record<FieldKind, () => ReactElement> = {
    chips: () => (
      <ChipInput
        label={`${meta.label} 편집`}
        value={toStringList(value)}
        onChange={onChange}
        separators={[","]}
        placeholder="입력 후 Enter"
      />
    ),
    text: () => (
      <Input
        aria-label={`${meta.label} 편집`}
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(event.target.value)}
      />
    ),
    number: () => (
      <Input
        type="number"
        min={0}
        inputMode="numeric"
        aria-label={`${meta.label} 편집`}
        className="tabular-nums"
        value={typeof value === "number" ? value : ""}
        onChange={(event) => {
          const parsed = Number(event.target.value);
          onChange(Number.isFinite(parsed) ? parsed : 0);
        }}
      />
    )
  };
  return editors[meta.kind]();
}
