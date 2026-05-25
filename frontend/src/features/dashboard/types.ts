import type {
  DashboardBidItem,
  DashboardOpportunityItem,
  DashboardResultItem
} from "@/shared/types";

export type DetailSelection =
  | { kind: "opportunity"; item: DashboardOpportunityItem }
  | { kind: "bid"; item: DashboardBidItem }
  | { kind: "result"; item: DashboardResultItem };
