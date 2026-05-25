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
import { MiniBar, MiniDonut } from "./Helpers";
import { StatusBadge } from "./StatusBadge";

export function OpportunityRow({
  item,
  onSelect
}: {
  item: DashboardOpportunityItem;
  onSelect: () => void;
}) {
  return (
    <button className="data-row" type="button" onClick={onSelect}>
      <div className="row-main">
        <div className="row-title">
          <span>{item.project.title}</span>
          <StatusBadge status={statusFromOpportunity(item)} label={labelOpportunityStatus(item)} />
        </div>
        <p>
          {item.source_label} · {item.project.issuing_agency ?? item.project.category ?? "입찰 후보"}
        </p>
      </div>
      <div className="row-side">
        <MiniBar value={item.priority_score} />
        <strong>{formatCurrencyCompact(item.recommended_amount)}</strong>
        <small>{formatHours(item.deadline_hours_remaining)}</small>
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
