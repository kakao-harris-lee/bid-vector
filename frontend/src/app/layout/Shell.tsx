import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Gavel,
  ListChecks,
  LogOut,
  RefreshCw,
  Send,
  Sliders,
  Trophy
} from "lucide-react";
import { Badge } from "@/shared/components/ui";
import { fetchDashboardSummary, queryKeys } from "@/shared/api";
import type { DashboardStatus, RouteKey } from "@/shared/types";
import { formatDate } from "@/shared/lib";
import { logoutSession, useAuthSession } from "./AuthGate";
import { BottomNav } from "./BottomNav";
import { IconButton } from "./IconButton";

export const ROUTE_LABELS: Record<RouteKey, { path: string; label: string; icon: typeof Gavel }> = {
  home: { path: "/dashboard", label: "오늘", icon: ListChecks },
  opportunities: { path: "/dashboard/opportunities", label: "입찰", icon: Gavel },
  bids: { path: "/dashboard/bids", label: "투찰", icon: Send },
  results: { path: "/dashboard/results", label: "결과", icon: Trophy }
};

export const STRATEGY_ROUTE_PATH = "/dashboard/strategy";

export function routeKeyFromPath(pathname: string): RouteKey {
  if (pathname.startsWith("/dashboard/bids")) return "bids";
  if (pathname.startsWith("/dashboard/opportunities")) return "opportunities";
  if (pathname.startsWith("/dashboard/results")) return "results";
  return "home";
}

/**
 * Active key for `BottomNav`. Returns `null` for paths that don't map to a
 * bottom-tab (e.g. `/dashboard/strategy`), so no tab is highlighted.
 * `routeKeyFromPath` keeps a `"home"` fallback for other consumers.
 */
export function bottomNavKeyForPath(pathname: string): RouteKey | null {
  if (pathname.startsWith(STRATEGY_ROUTE_PATH)) return null;
  return routeKeyFromPath(pathname);
}

function pageTitleForPath(pathname: string): string {
  if (pathname.startsWith(STRATEGY_ROUTE_PATH)) return "전략 편집";
  const route = routeKeyFromPath(pathname);
  if (route === "home") return "오늘 할 일";
  return ROUTE_LABELS[route].label;
}

export function Shell() {
  const session = useAuthSession();
  const location = useLocation();
  const navigate = useNavigate();
  const route = routeKeyFromPath(location.pathname);
  const bottomNavRoute = bottomNavKeyForPath(location.pathname);
  const [reloadKey, setReloadKey] = useState(0);

  const summary = useQuery({
    queryKey: [...queryKeys.dashboard.summary(), reloadKey],
    queryFn: () => fetchDashboardSummary(session?.token),
    enabled: Boolean(session?.token)
  });

  useEffect(() => {
    if (summary.error && (summary.error as { status?: number }).status === 401) {
      logoutSession();
    }
  }, [summary.error]);

  const headerDate = summary.data?.today ?? new Date().toISOString();
  const pageTitle = pageTitleForPath(location.pathname);
  const onStrategy = location.pathname.startsWith(STRATEGY_ROUTE_PATH);

  return (
    <div className="flex min-h-screen flex-col bg-[var(--color-bg)] pb-20 text-[var(--color-fg)]">
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-card)]/95 px-4 py-3 backdrop-blur">
        <button
          className="flex items-center gap-3 text-left text-sm focus-visible:outline-none"
          type="button"
          onClick={() => navigate(ROUTE_LABELS.home.path)}
        >
          <span className="grid h-9 w-9 place-items-center rounded-md bg-[var(--color-primary)] text-xs font-bold text-[var(--color-primary-foreground)]">
            BV
          </span>
          <span className="flex flex-col">
            <strong className="text-base font-semibold">{pageTitle}</strong>
            <small className="text-xs text-[var(--color-muted)]">{formatDate(headerDate)}</small>
          </span>
        </button>
        <div className="flex items-center gap-2">
          {summary.data ? (
            <Badge tone={toneFromStatus(summary.data.operational_status.status)}>
              {summary.data.operational_status.label}
            </Badge>
          ) : null}
          <IconButton
            label="전략 편집"
            onClick={() => navigate(onStrategy ? ROUTE_LABELS.home.path : STRATEGY_ROUTE_PATH)}
          >
            <Sliders size={18} />
          </IconButton>
          <IconButton
            label="새로고침"
            onClick={() => setReloadKey((value) => value + 1)}
            disabled={summary.isFetching}
          >
            <RefreshCw size={18} />
          </IconButton>
          <IconButton label="로그아웃" onClick={() => logoutSession()}>
            <LogOut size={18} />
          </IconButton>
        </div>
      </header>

      <main className="flex-1">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-6">
          {summary.error && (summary.error as { status?: number }).status !== 401 ? (
            <InlineNotice tone="critical">
              {(summary.error as Error).message ?? "대시보드를 불러오지 못했습니다."}
            </InlineNotice>
          ) : null}
          <Outlet context={{ summary, session, route, reloadKey }} />
        </div>
      </main>

      <BottomNav route={bottomNavRoute} />
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

export function StatusIcon({ status }: { status: DashboardStatus }) {
  if (status === "healthy") return <CheckCircle2 className="text-[var(--color-success)]" size={20} />;
  if (status === "critical") return <AlertTriangle className="text-[var(--color-danger)]" size={20} />;
  if (status === "watch") return <Clock3 className="text-[var(--color-warn)]" size={20} />;
  return <Activity className="text-[var(--color-info)]" size={20} />;
}

export function toneFromStatus(
  status: DashboardStatus | "critical" | "watch" | "info"
): "info" | "healthy" | "watch" | "critical" {
  if (status === "healthy") return "healthy";
  if (status === "critical") return "critical";
  if (status === "watch") return "watch";
  return "info";
}
