import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { DashboardWorkItem, RouteKey } from "@/shared/types";
import { useShellContext } from "@/app/dashboardContext";
import { ROUTE_LABELS } from "@/app/layout/Shell";
import {
  DetailDrawer,
  EmptyState,
  ItemList,
  LoadingState,
  MetricTile,
  SectionHeader,
  SegmentedTabs,
  WorkItemCard
} from "./components";
import type { DetailSelection } from "./types";

export function HomeScreen() {
  const { summary, session } = useShellContext();
  const navigate = useNavigate();
  const [activePreview, setActivePreview] = useState<RouteKey>("opportunities");
  const [selected, setSelected] = useState<DetailSelection | null>(null);

  if (summary.isPending && !summary.data) return <LoadingState />;
  if (!summary.data) {
    return <EmptyState title="대시보드 데이터 없음" detail="표시할 데이터가 없습니다." />;
  }

  return (
    <>
      <HomeContent
        summary={summary.data}
        activePreview={activePreview}
        onPreviewChange={setActivePreview}
        onNavigate={(route) => navigate(ROUTE_LABELS[route].path)}
        onSelect={setSelected}
      />
      <DetailDrawer
        selection={selected}
        onClose={() => setSelected(null)}
        username={session?.username ?? null}
        authToken={session?.token ?? null}
      />
    </>
  );
}

function HomeContent({
  summary,
  activePreview,
  onPreviewChange,
  onNavigate,
  onSelect
}: {
  summary: NonNullable<ReturnType<typeof useShellContext>["summary"]["data"]>;
  activePreview: RouteKey;
  onPreviewChange: (route: RouteKey) => void;
  onNavigate: (route: RouteKey) => void;
  onSelect: (selection: DetailSelection) => void;
}) {
  const previewItems = useMemo(() => {
    if (activePreview === "bids") return summary.recent_bids;
    if (activePreview === "results") return summary.recent_results;
    return summary.recent_opportunities;
  }, [activePreview, summary]);

  const visibleWorkItems = summary.work_items.slice(0, 3);
  const hasMoreWorkItems = summary.work_items.length > visibleWorkItems.length;

  return (
    <>
      <section className="metric-strip" aria-label="핵심 지표">
        {summary.metrics.map((metric) => (
          <MetricTile key={metric.key} metric={metric} />
        ))}
      </section>

      <section>
        <SegmentedTabs active={activePreview} onChange={onPreviewChange} sections={summary.sections} />
        <ItemList route={activePreview} items={previewItems} onSelect={onSelect} compact />
      </section>

      <section className="work-section">
        <SectionHeader title="오늘 할 일" count={summary.work_items.length} />
        {summary.work_items.length ? (
          <>
            <div className="work-list">
              {visibleWorkItems.map((item) => (
                <WorkItemCard
                  key={item.key}
                  item={item}
                  onOpen={() => onNavigate(routeFromWorkItem(item))}
                />
              ))}
            </div>
            {hasMoreWorkItems ? (
              <button
                className="more-action"
                type="button"
                onClick={() => onNavigate(routeFromWorkItem(summary.work_items[0]))}
              >
                전체 {summary.work_items.length}건 보기
              </button>
            ) : null}
          </>
        ) : (
          <EmptyState title="오늘 할 일 없음" detail="긴급 항목이 없습니다." />
        )}
      </section>
    </>
  );
}

function routeFromWorkItem(item: DashboardWorkItem): RouteKey {
  if (item.item_type === "bid_pending_result") return "bids";
  if (item.item_type === "result_review") return "results";
  return "opportunities";
}
