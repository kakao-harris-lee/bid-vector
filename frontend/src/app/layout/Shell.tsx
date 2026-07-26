import { useMemo, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchDashboardSummary, queryKeys } from "@/shared/api";
import { useAuthSession } from "./AuthGate";
import { BottomNav } from "./BottomNav";
import { NotificationDrawer, useRealtimeEvents } from "@/features/realtime";
import { SessionExpiredModal } from "./SessionExpiredModal";
import { useActiveOperator } from "@/app/operatorContext";
import {
  ADMIN_HOME_ROUTE_PATH,
  ROUTE_LABELS,
  bottomNavKeyForPath,
  pageTitleForPath,
  routeKeyFromPath
} from "./shellRoutes";
import { ActiveOperatorContextBar, ShellHeader } from "./components";

export {
  ROUTE_LABELS,
  PROFILE_ROUTE_PATH,
  ONBOARDING_ROUTE_PATH,
  ONBOARDING_HISTORY_ROUTE_PATH,
  STRATEGY_ROUTE_PATH,
  PROJECTS_ROUTE_PATH,
  GUIDE_ROUTE_PATH,
  ADMIN_HOME_ROUTE_PATH,
  DECISIONS_ROUTE_PATH,
  EXPERIMENTS_ROUTE_PATH,
  SYNTHETIC_ROUTE_PATH,
  OPERATIONS_ROUTE_PATH,
  ACCURACY_REPORT_ROUTE_PATH,
  DECISION_SAMPLES_ROUTE_PATH,
  routeKeyFromPath,
  bottomNavKeyForPath
} from "./shellRoutes";
export { StatusIcon, toneFromStatus } from "./shellStatus";

export function Shell({ surface = "user" }: { surface?: "user" | "admin" }) {
  const session = useAuthSession();
  const location = useLocation();
  const route = routeKeyFromPath(location.pathname);
  const bottomNavRoute = bottomNavKeyForPath(location.pathname);
  const [reloadKey, setReloadKey] = useState(0);
  const activeOperator = useActiveOperator(session);
  const isAdminSurface = surface === "admin";
  const activeOperatorId = isAdminSurface ? activeOperator.activeOperatorId : null;
  const surfaceActiveOperator = useMemo(() => {
    if (isAdminSurface) return activeOperator;
    const currentOperator =
      activeOperator.currentOperatorId === null
        ? null
        : activeOperator.operators.find(
            (row) => row.operator_id === activeOperator.currentOperatorId
          ) ?? null;
    return { ...activeOperator, activeOperatorId: null, currentOperator };
  }, [activeOperator, isAdminSurface]);
  const homePath = isAdminSurface ? ADMIN_HOME_ROUTE_PATH : ROUTE_LABELS.home.path;

  const summary = useQuery({
    queryKey: [...queryKeys.dashboard.summary(activeOperatorId), reloadKey],
    queryFn: () => fetchDashboardSummary(session?.token, activeOperatorId),
    enabled: Boolean(session?.token)
  });

  // 401 is now handled by SessionExpiredModal (triggered from apiRequest via
  // the `bid-vector:session-expired` event). The Shell no longer hard-logs out
  // on 401 so the user can re-auth in place without losing screen state.

  const headerDate = summary.data?.today ?? new Date().toISOString();
  const pageTitle = pageTitleForPath(location.pathname);
  const [notificationsOpen, setNotificationsOpen] = useState(false);

  useRealtimeEvents(session);

  return (
    <div className="flex min-h-screen flex-col bg-[var(--color-bg)] pb-20 text-[var(--color-fg)]">
      <ShellHeader
        pageTitle={pageTitle}
        headerDate={headerDate}
        homePath={homePath}
        isAdminSurface={isAdminSurface}
        activeOperator={activeOperator}
        operationalStatus={summary.data?.operational_status ?? null}
        refreshing={summary.isFetching}
        pathname={location.pathname}
        onOpenNotifications={() => setNotificationsOpen(true)}
        onRefresh={() => setReloadKey((value) => value + 1)}
      />

      <main className="flex-1">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-6">
          <ActiveOperatorContextBar
            activeOperator={surfaceActiveOperator}
            summaryOperatorUsername={summary.data?.current_operator_username ?? null}
            pathname={location.pathname}
            enabled={isAdminSurface}
          />
          {summary.error && (summary.error as { status?: number }).status !== 401 ? (
            <InlineNotice tone="critical">
              {(summary.error as Error).message ?? "대시보드를 불러오지 못했습니다."}
            </InlineNotice>
          ) : null}
          <Outlet context={{ summary, session, route, reloadKey, activeOperator: surfaceActiveOperator }} />
        </div>
      </main>

      {isAdminSurface ? null : <BottomNav route={bottomNavRoute} />}
      <NotificationDrawer
        open={notificationsOpen}
        onClose={() => setNotificationsOpen(false)}
        session={session}
      />
      <SessionExpiredModal />
    </div>
  );
}

function InlineNotice({
  tone,
  children
}: {
  tone: "critical" | "watch" | "info";
  children: React.ReactNode;
}) {
  const toneClass =
    tone === "critical"
      ? "border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_82%)] text-[var(--color-danger)]"
      : tone === "watch"
        ? "border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_82%)] text-[var(--color-warn)]"
        : "border-[var(--color-info)] bg-[color-mix(in_oklch,var(--color-info),white_82%)] text-[var(--color-info)]";
  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${toneClass}`} role="alert">
      {children}
    </div>
  );
}
