import { Badge } from "@/shared/components/ui";
import { formatCurrencyCompact, formatPercent } from "@/shared/lib";
import { LabeledStat } from "@/shared/components";
import type { DecisionFunnelRecentSubmissionItem } from "@/shared/types/decisions";

export function DecisionEvidenceChecklist({ item }: { item: DecisionFunnelRecentSubmissionItem }) {
  const strengths = item.strengths ?? [];
  const risks = item.risk_flags ?? [];
  return (
    <div
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-secondary)] p-3"
      aria-label={`${item.project_title} 추천 근거 확인`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-[var(--color-fg)]">추천가 근거 확인</span>
        <Badge tone={risks.length > 0 ? "watch" : "healthy"}>
          리스크 {risks.length.toLocaleString("ko-KR")}개
        </Badge>
      </div>
      <dl className="grid gap-2 sm:grid-cols-3">
        <LabeledStat variant="evidence" label="추천가" value={formatCurrencyCompact(item.recommended_amount)} />
        <LabeledStat variant="evidence" label="우선순위" value={formatPercent(item.priority_score)} />
        <LabeledStat
          variant="evidence"
          label="근거 신호"
          value={`강점 ${strengths.length.toLocaleString("ko-KR")}개 / 리스크 ${risks.length.toLocaleString(
            "ko-KR"
          )}개`}
        />
      </dl>
      {(strengths.length > 0 || risks.length > 0) ? (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <SignalList title="강점" items={strengths} empty="기록된 강점 신호 없음" />
          <SignalList title="리스크" items={risks} empty="기록된 리스크 신호 없음" />
        </div>
      ) : null}
      <p className="mt-2 text-[11px] leading-tight text-[var(--color-muted)]">
        투찰 요약에서 예측 가격대와 하한율 참고값을 확인하고, 운영 KPI의 과거 추천 오차와
        함께 검토하세요.
      </p>
    </div>
  );
}

function SignalList({
  title,
  items,
  empty
}: {
  title: string;
  items: string[];
  empty: string;
}) {
  return (
    <div>
      <p className="mb-1 text-[11px] font-medium text-[var(--color-muted)]">{title}</p>
      {items.length > 0 ? (
        <ul className="flex flex-wrap gap-1" aria-label={`${title} 신호`}>
          {items.map((signal) => (
            <li
              key={signal}
              className="rounded-full bg-[var(--color-card)] px-2 py-0.5 text-[11px] text-[var(--color-fg)]"
            >
              {signal}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[11px] text-[var(--color-muted)]">{empty}</p>
      )}
    </div>
  );
}
