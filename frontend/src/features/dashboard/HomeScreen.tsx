import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { DashboardWorkItem, RouteKey } from "@/shared/types";
import { useShellContext } from "@/app/dashboardContext";
import { PROFILE_ROUTE_PATH, ROUTE_LABELS } from "@/app/layout/Shell";
import { useProfileQuery } from "@/features/profile/hooks";
import {
  DetailDrawer,
  EmptyState,
  ItemList,
  LoadingState,
  MetricTile,
  ProfileStatusWidget,
  SectionHeader,
  SegmentedTabs,
  WorkItemCard
} from "./components";
import type { DetailSelection } from "./types";

export function HomeScreen() {
  const { summary, session, activeOperator } = useShellContext();
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
        session={session}
        activeOperatorId={activeOperator.activeOperatorId}
        currentOperator={activeOperator.currentOperator}
        onOpenProfile={() => navigate(PROFILE_ROUTE_PATH)}
      />
      <DetailDrawer
        selection={selected}
        onClose={() => setSelected(null)}
        username={session?.username ?? null}
        authToken={session?.token ?? null}
        session={session}
        activeOperatorId={activeOperator.activeOperatorId}
      />
    </>
  );
}

function HomeContent({
  summary,
  activePreview,
  onPreviewChange,
  onNavigate,
  onSelect,
  session,
  activeOperatorId,
  currentOperator,
  onOpenProfile
}: {
  summary: NonNullable<ReturnType<typeof useShellContext>["summary"]["data"]>;
  activePreview: RouteKey;
  onPreviewChange: (route: RouteKey) => void;
  onNavigate: (route: RouteKey) => void;
  onSelect: (selection: DetailSelection) => void;
  session: ReturnType<typeof useShellContext>["session"];
  activeOperatorId: number | null;
  currentOperator: ReturnType<
    typeof useShellContext
  >["activeOperator"]["currentOperator"];
  onOpenProfile: () => void;
}) {
  const previewItems = useMemo(() => {
    if (activePreview === "bids") return summary.recent_bids;
    if (activePreview === "results") return summary.recent_results;
    return summary.recent_opportunities;
  }, [activePreview, summary]);

  const visibleWorkItems = summary.work_items.slice(0, 3);
  const hasMoreWorkItems = summary.work_items.length > visibleWorkItems.length;

  // PR #74 makes `/operator/profile?operator_id=` a cross-operator GET for
  // privileged callers. The 5-axis detail is now fetched regardless of which
  // company the user is viewing — only the *edit* CTA stays gated to the
  // token owner. `null` means "fall back to the token owner" so the URL omits
  // `?operator_id=` entirely.
  const isOwnContext = activeOperatorId === null;
  const profileQuery = useProfileQuery(session, activeOperatorId);

  return (
    <>
      <section aria-label="내 자격 상태 요약">
        <ProfileStatusWidget
          operatorAccount={currentOperator}
          profile={profileQuery.data ?? null}
          isOwnContext={isOwnContext}
          isProfileLoading={profileQuery.isPending}
          onWizardEnter={onOpenProfile}
          onEdit={onOpenProfile}
        />
      </section>

      <section className="metric-strip" aria-label="핵심 지표">
        {summary.metrics.map((metric) => (
          <MetricTile key={metric.key} metric={metric} />
        ))}
      </section>

      <section>
        <SegmentedTabs active={activePreview} onChange={onPreviewChange} sections={summary.sections} />
        <ItemList
          route={activePreview}
          items={previewItems}
          onSelect={onSelect}
          compact
          session={session}
          activeOperatorId={activeOperatorId}
        />
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
