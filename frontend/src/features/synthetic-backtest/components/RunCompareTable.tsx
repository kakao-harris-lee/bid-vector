import { Badge } from "@/shared/components/ui";
import type { SyntheticExperimentCompareOperator } from "@/shared/types/synthetic";
import {
  DELTA_FIELDS,
  deltaToneClass,
  formatDelta,
  pct,
  sideValue
} from "../runCompare.helpers";

export function RunCompareTable({
  operators
}: {
  operators: SyntheticExperimentCompareOperator[];
}) {
  if (operators.length === 0) {
    return (
      <p className="py-2 text-center text-[var(--color-muted)]">
        두 런에 공통으로 존재하는 회사가 없습니다.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
            <th className="py-1">회사(slug)</th>
            {DELTA_FIELDS.map((field) => (
              <th key={field.key} className="py-1 text-right" colSpan={3}>
                {field.label}
              </th>
            ))}
          </tr>
          <tr className="border-b border-[var(--color-border)] text-right text-[10px] text-[var(--color-muted)]">
            <th className="py-1 text-left" />
            {DELTA_FIELDS.map((field) => (
              <SubHeaderCells key={field.key} />
            ))}
          </tr>
        </thead>
        <tbody>
          {operators.map((operator) => (
            <OperatorRow key={operator.operator_slug} operator={operator} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SubHeaderCells() {
  return (
    <>
      <th className="py-1 text-right font-normal">A</th>
      <th className="py-1 text-right font-normal">B</th>
      <th className="py-1 text-right font-normal">Δ</th>
    </>
  );
}

function OperatorRow({ operator }: { operator: SyntheticExperimentCompareOperator }) {
  return (
    <tr
      className="border-b border-[var(--color-border)]/60"
      aria-label={`${operator.operator_slug} 비교 행`}
    >
      <td className="py-1 pr-2 font-medium text-[var(--color-fg)]">
        {operator.operator_slug}
      </td>
      {DELTA_FIELDS.map((field) => {
        const deltaValue = operator.delta[field.key];
        const deltaText = formatDelta(deltaValue);
        return (
          <DeltaCells
            key={field.key}
            aValue={sideValue(operator.a, field.key)}
            bValue={sideValue(operator.b, field.key)}
            deltaText={deltaText}
            toneClass={deltaToneClass(deltaValue, field.higherIsBetter)}
          />
        );
      })}
    </tr>
  );
}

function DeltaCells({
  aValue,
  bValue,
  deltaText,
  toneClass
}: {
  aValue: number | null | undefined;
  bValue: number | null | undefined;
  deltaText: string | null;
  toneClass: string;
}) {
  return (
    <>
      <td className="py-1 text-right tabular-nums">{pct(aValue)}</td>
      <td className="py-1 text-right tabular-nums">{pct(bValue)}</td>
      <td className="py-1 text-right tabular-nums">
        {deltaText === null ? (
          <Badge tone="muted">비교불가</Badge>
        ) : (
          <span className={`font-medium ${toneClass}`}>{deltaText}</span>
        )}
      </td>
    </>
  );
}
