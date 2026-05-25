import { BarChart3, Clock3, Send } from "lucide-react";
import type { DashboardWorkItem } from "@/shared/types";
import { labelWorkItemStatus } from "@/shared/lib";
import { StatusBadge } from "./StatusBadge";

export function WorkItemCard({
  item,
  onOpen
}: {
  item: DashboardWorkItem;
  onOpen: () => void;
}) {
  return (
    <button className="work-card" type="button" onClick={onOpen}>
      <div className="work-icon">
        {item.item_type === "opportunity_due" ? <Clock3 size={18} /> : null}
        {item.item_type === "bid_pending_result" ? <Send size={18} /> : null}
        {item.item_type === "result_review" ? <BarChart3 size={18} /> : null}
      </div>
      <div>
        <strong>{item.title}</strong>
        <span>{item.subtitle}</span>
      </div>
      <StatusBadge status={item.severity} label={labelWorkItemStatus(item)} />
    </button>
  );
}
