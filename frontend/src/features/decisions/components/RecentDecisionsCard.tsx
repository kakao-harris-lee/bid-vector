import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatCurrencyCompact, formatDateTime, formatPercent } from "@/shared/lib";
import { InlineActionButtons, ReasonIndicators } from "@/features/dashboard/components";
import type { BidDecisionActionType } from "@/shared/api";
import type { DecisionFunnelResponse } from "@/shared/types/decisions";
import {
  ACTION_LABEL,
  ACTION_TONE,
  DECISION_STATUS_LABEL
} from "@/shared/constants/decisionLabels";
import { DecisionEvidenceChecklist } from "./DecisionEvidenceChecklist";
import { DecisionReasonDraft } from "./DecisionReasonDraft";

export function RecentDecisionsCard({
  funnel,
  reasonDraftByRecord,
  pendingActionByRecord,
  onReasonChange,
  onAction,
  onNavigateSummary
}: {
  funnel?: DecisionFunnelResponse;
  reasonDraftByRecord: Record<number, string>;
  pendingActionByRecord: Record<number, BidDecisionActionType | null>;
  onReasonChange: (recordId: number, next: string) => void;
  onAction: (decisionRecordId: number, action: BidDecisionActionType) => void;
  onNavigateSummary: (decisionRecordId: number) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          최근 결정
          {funnel ? (
            <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
              (제출 {funnel.submitted_count}건)
            </span>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        {funnel && funnel.recent_submissions.length === 0 ? (
          <p className="text-[var(--color-muted)]">최근 제출 결정이 없습니다.</p>
        ) : null}
        {funnel?.recent_submissions.map((item) => {
          const reasonDraft = reasonDraftByRecord[item.decision_record_id] ?? "";
          const hasReasonDraft = reasonDraft.trim().length > 0;
          return (
            <article
              key={item.decision_record_id}
              className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate font-medium text-[var(--color-fg)]" title={item.project_title}>
                    {item.project_title}
                  </span>
                  <ReasonIndicators strengths={item.strengths} riskFlags={item.risk_flags} />
                </span>
                <div className="flex items-center gap-1">
                  <Badge tone={ACTION_TONE[item.action]}>{ACTION_LABEL[item.action]}</Badge>
                  <Badge tone="info">{DECISION_STATUS_LABEL[item.decision_status]}</Badge>
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-1 text-[var(--color-muted)]">
                <span>
                  {item.category ?? "-"} · 우선순위 {formatPercent(item.priority_score)} · {formatDateTime(item.updated_at)}
                </span>
                <span className="tabular-nums">{formatCurrencyCompact(item.recommended_amount)}</span>
              </div>
              <DecisionEvidenceChecklist item={item} />
              <DecisionReasonDraft
                recordId={item.decision_record_id}
                value={reasonDraft}
                onChange={(next) => onReasonChange(item.decision_record_id, next)}
              />
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <InlineActionButtons
                  decisionStatus={item.decision_status}
                  pendingAction={pendingActionByRecord[item.decision_record_id] ?? null}
                  disabled={!hasReasonDraft}
                  onAction={(action) => onAction(item.decision_record_id, action)}
                />
                <span className="text-[11px] text-[var(--color-muted)]">
                  {hasReasonDraft ? "선택 사유 작성됨" : "선택 사유 작성 후 전환 가능"}
                </span>
                <button
                  type="button"
                  onClick={() => onNavigateSummary(item.decision_record_id)}
                  className="ml-auto rounded-md border border-[var(--color-border)] px-2 py-1 text-[var(--color-fg)] hover:bg-[var(--color-secondary)]"
                >
                  투찰 요약
                </button>
              </div>
            </article>
          );
        })}
      </CardContent>
    </Card>
  );
}
