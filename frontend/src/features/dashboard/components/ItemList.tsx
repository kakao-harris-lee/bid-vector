import type {
  DashboardBidItem,
  DashboardOpportunityItem,
  DashboardResultItem,
  ListItem,
  RouteKey
} from "@/shared/types";
import { EmptyState } from "./Helpers";
import { BidRow, OpportunityRow, ResultRow } from "./Rows";
import type { DetailSelection } from "../types";

export function ItemList({
  route,
  items,
  onSelect,
  compact = false
}: {
  route: RouteKey;
  items: ListItem[];
  onSelect: (selection: DetailSelection) => void;
  compact?: boolean;
}) {
  if (!items.length) {
    const emptyState = emptyStateForRoute(route);
    return <EmptyState title={emptyState.title} detail={emptyState.detail} />;
  }

  return (
    <div className={compact ? "list compact" : "list"}>
      {items.map((item) => {
        if (route === "bids" && "bid_id" in item) {
          const bid = item as DashboardBidItem;
          return <BidRow key={bid.bid_id} item={bid} onSelect={() => onSelect({ kind: "bid", item: bid })} />;
        }
        if (route === "results" && "tender_result_id" in item) {
          const result = item as DashboardResultItem;
          return (
            <ResultRow
              key={result.tender_result_id}
              item={result}
              onSelect={() => onSelect({ kind: "result", item: result })}
            />
          );
        }
        if ("action" in item && !("bid_id" in item)) {
          const opportunity = item as DashboardOpportunityItem;
          return (
            <OpportunityRow
              key={`${opportunity.source}:${opportunity.decision_record_id ?? opportunity.paper_bid_id}`}
              item={opportunity}
              onSelect={() => onSelect({ kind: "opportunity", item: opportunity })}
            />
          );
        }
        return null;
      })}
    </div>
  );
}

function emptyStateForRoute(route: RouteKey): { title: string; detail: string } {
  if (route === "opportunities") {
    return {
      title: "입찰 후보 없음",
      detail: "저장된 입찰 판단이나 최신 페이퍼 후보가 없습니다."
    };
  }
  if (route === "bids") {
    return {
      title: "실제 투찰 기록 없음",
      detail: "제출되었거나 검토 중인 실제 투찰 기록이 없습니다."
    };
  }
  if (route === "results") {
    return {
      title: "결과 없음",
      detail: "최종 낙찰 결과가 수집된 항목이 없습니다."
    };
  }
  return { title: "목록 없음", detail: "표시할 항목이 없습니다." };
}
