import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import type { DecisionFunnelBreakdownItem } from "@/shared/types/decisions";
import { BreakdownChart } from "./BreakdownChart";

type BreakdownDimension = "category" | "workload" | "agency";

export function SegmentBreakdownCard({
  items,
  dimension,
  onDimensionChange
}: {
  items: DecisionFunnelBreakdownItem[];
  dimension: BreakdownDimension;
  onDimensionChange: (dimension: BreakdownDimension) => void;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>세그먼트 breakdown</CardTitle>
        <div role="tablist" aria-label="세그먼트 차원" className="flex gap-1 text-xs">
          {(["category", "workload", "agency"] as const).map((dim) => (
            <button
              key={dim}
              type="button"
              role="tab"
              aria-selected={dimension === dim}
              onClick={() => onDimensionChange(dim)}
              className={`rounded-md border px-2 py-1 ${
                dimension === dim
                  ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]"
                  : "border-[var(--color-border)] text-[var(--color-muted)]"
              }`}
            >
              {dim === "category" ? "카테고리" : dim === "workload" ? "워크로드" : "기관"}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        <BreakdownChart items={items} />
      </CardContent>
    </Card>
  );
}
