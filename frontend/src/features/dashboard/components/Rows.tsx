import { useState } from "react";
import type {
  DashboardBidItem,
  DashboardOpportunityItem,
  DashboardResultItem
} from "@/shared/types";
import {
  formatCurrencyCompact,
  formatDate,
  formatHours,
  formatPercent,
  labelBidStatus,
  labelDecisionStatus,
  labelOpportunityStatus,
  labelOutcome,
  statusFromBid,
  statusFromOpportunity,
  statusFromOutcome
} from "@/shared/lib";
import { AmountWithBasis } from "@/shared/components";
import type { BidDecisionActionType } from "@/shared/api";
import { MiniBar, MiniDonut } from "./Helpers";
import { StatusBadge } from "./StatusBadge";
import { ReasonIndicators } from "./ReasonIndicators";
import { InlineActionButtons } from "./InlineActionButtons";
import { useApplyBidDecisionActionMutation } from "../hooks";
import type { AuthSession } from "@/app/layout/AuthGate";

export interface OpportunityRowProps {
  item: DashboardOpportunityItem;
  onSelect: () => void;
  /**
   * When supplied, the row renders inline submit/review/skip buttons for
   * decision-source items. Paper-bid items skip the buttons (simulation only).
   * Omitting these props keeps legacy callers (tests, screenshots) unchanged.
   */
  session?: AuthSession | null;
  activeOperatorId?: number | null;
}

export function OpportunityRow({
  item,
  onSelect,
  session,
  activeOperatorId = null
}: OpportunityRowProps) {
  const canAct =
    item.source === "decision" &&
    typeof item.decision_record_id === "number" &&
    item.decision_record_id > 0 &&
    Boolean(session?.token);
  const mutation = useApplyBidDecisionActionMutation(session ?? null);
  const [pendingAction, setPendingAction] = useState<BidDecisionActionType | null>(
    null
  );

  const handleAction = (action: BidDecisionActionType) => {
    if (!canAct || !item.decision_record_id) return;
    setPendingAction(action);
    mutation.mutate(
      {
        decisionRecordId: item.decision_record_id,
        action,
        operatorId: activeOperatorId
      },
      {
        onSettled: () => setPendingAction(null)
      }
    );
  };

  return (
    <button className="data-row" type="button" onClick={onSelect}>
      <div className="row-main">
        <div className="row-title">
          <span>{item.project.title}</span>
          <StatusBadge status={statusFromOpportunity(item)} label={labelOpportunityStatus(item)} />
          <ReasonIndicators strengths={item.strengths} riskFlags={item.risk_flags} />
        </div>
        <p>
          {item.source_label} · {item.project.issuing_agency ?? item.project.category ?? "입찰 후보"}
        </p>
      </div>
      <div className="row-side">
        <MiniBar value={item.priority_score} />
        <strong>
          {/* 라벨 없는 숫자라 basis 뱃지가 유일한 단서다 — 추정가격으로 읽히면 안 된다. */}
          <AmountWithBasis
            amount={item.recommended_amount}
            basis="submission"
            variant="inline"
            compact
          />
        </strong>
        <small>{formatHours(item.deadline_hours_remaining)}</small>
        {canAct ? (
          <InlineActionButtons
            decisionStatus={item.decision_status}
            pendingAction={pendingAction}
            compact
            onAction={handleAction}
          />
        ) : null}
      </div>
    </button>
  );
}

export function BidRow({
  item,
  onSelect
}: {
  item: DashboardBidItem;
  onSelect: () => void;
}) {
  return (
    <button className="data-row" type="button" onClick={onSelect}>
      <div className="row-main">
        <div className="row-title">
          <span>{item.project.title}</span>
          <StatusBadge status={statusFromBid(item.status)} label={labelBidStatus(item.status)} />
        </div>
        <p>{formatDate(item.submitted_at)} 제출</p>
      </div>
      <div className="row-side">
        <MiniDonut value={item.score ?? 0.64} />
        <strong>{formatCurrencyCompact(item.bid_amount)}</strong>
        <small>{item.decision_status ? labelDecisionStatus(item.decision_status) : "판단 없음"}</small>
      </div>
    </button>
  );
}

export function ResultRow({
  item,
  onSelect
}: {
  item: DashboardResultItem;
  onSelect: () => void;
}) {
  const errorRate = item.recommendation_error_rate ?? item.prediction_error_rate ?? 0;
  return (
    <button className="data-row" type="button" onClick={onSelect}>
      <div className="row-main">
        <div className="row-title">
          <span>{item.project.title}</span>
          <StatusBadge status={statusFromOutcome(item.award_outcome)} label={labelOutcome(item.award_outcome)} />
        </div>
        <p>{item.winning_company ?? item.result_status}</p>
      </div>
      <div className="row-side">
        <MiniBar value={Math.min(errorRate * 10, 1)} tone="amber" />
        <strong>{formatCurrencyCompact(item.winning_amount)}</strong>
        <small>오차 {formatPercent(errorRate)}</small>
      </div>
    </button>
  );
}
