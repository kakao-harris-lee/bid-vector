import type {
  PaperBiddingRunListItem,
  PaperBiddingSettlementOverview,
  PaperBiddingSummaryResponse
} from "@/shared/types";
import {
  formatPercent,
  formatSettlementMilestone,
  numberFromSummary,
  statusFromSettlement
} from "@/shared/lib";
import { StatusBadge } from "./StatusBadge";

export function BacktestSummaryPanel({
  summary
}: {
  summary: PaperBiddingSummaryResponse | null | undefined;
}) {
  const latestRun = summary?.latest_run;
  if (!latestRun) {
    return (
      <div className="backtest-panel">
        <div>
          <span>페이퍼 검증</span>
          <strong>실행 없음</strong>
        </div>
        <p>저장된 백테스트나 forward paper 실행이 없습니다.</p>
      </div>
    );
  }

  const averageBidRateError = numberFromSummary(latestRun.summary.average_absolute_bid_rate_error);
  const closeCount = numberFromSummary(latestRun.summary.within_0_3pct_count);
  const settlementOverview = latestRun.settlement_overview;
  const hasSettledResults = latestRun.settled_count > 0;

  return (
    <div className="backtest-panel">
      <div className="backtest-head">
        <div>
          <span>{latestRun.mode === "forward_paper" ? "Forward Paper" : "Historical Backtest"}</span>
          <strong>{latestRun.paper_bid_count}건 검증</strong>
        </div>
        <StatusBadge
          status={
            latestRun.status === "completed"
              ? "healthy"
              : latestRun.status === "failed"
                ? "critical"
                : "watch"
          }
          label={latestRun.status}
        />
      </div>
      <div className="backtest-grid">
        <div>
          <span>후보</span>
          <strong>{latestRun.candidate_count}</strong>
        </div>
        <div>
          <span>정산</span>
          <strong>
            {latestRun.settled_count}/{latestRun.paper_bid_count}
          </strong>
        </div>
        <div>
          <span>0.3% 이내</span>
          <strong>{hasSettledResults ? (closeCount ?? 0) : "정산 없음"}</strong>
        </div>
        <div>
          <span>평균 오차</span>
          <strong>{hasSettledResults ? formatPercent(averageBidRateError) : "정산 없음"}</strong>
        </div>
      </div>
      {settlementOverview ? <SettlementOverview overview={settlementOverview} /> : null}
    </div>
  );
}

function SettlementOverview({ overview }: { overview: PaperBiddingSettlementOverview }) {
  return (
    <div className="settlement-overview">
      <div className="settlement-overview-head">
        <div>
          <span>승패 확정</span>
          <strong>{overview.label}</strong>
        </div>
        <StatusBadge status={statusFromSettlement(overview.status)} label={overview.label} />
      </div>
      <p>{overview.detail}</p>
      <div className="settlement-overview-meta">
        <span>{formatSettlementMilestone(overview)}</span>
        <span>
          대기 {overview.unsettled_count}건 · 결과 입수 {overview.ready_to_settle_count}건 · 마감 전 {overview.before_deadline_count}건
        </span>
      </div>
    </div>
  );
}

export type { PaperBiddingRunListItem };
