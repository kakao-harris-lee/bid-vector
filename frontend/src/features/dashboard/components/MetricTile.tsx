import type { DashboardMetric } from "@/shared/types";
import { formatMetricValue } from "@/shared/lib";
import { StatusIcon } from "./StatusIcon";

export function MetricTile({ metric }: { metric: DashboardMetric }) {
  return (
    <article className="metric-tile">
      <div>
        <span>{metric.label}</span>
        <strong>{formatMetricValue(metric)}</strong>
      </div>
      <StatusIcon status={metric.status} />
    </article>
  );
}
