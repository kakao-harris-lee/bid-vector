import { Gavel, ListChecks, Send, Trophy } from "lucide-react";
import type { RouteKey } from "@/shared/types";

export const ROUTE_LABELS: Record<RouteKey, { path: string; label: string; icon: typeof Gavel }> = {
  home: { path: "/dashboard", label: "오늘", icon: ListChecks },
  opportunities: { path: "/dashboard/opportunities", label: "입찰", icon: Gavel },
  bids: { path: "/dashboard/bids", label: "투찰", icon: Send },
  results: { path: "/dashboard/results", label: "결과", icon: Trophy }
};

export const PROFILE_ROUTE_PATH = "/dashboard/profile";
export const ONBOARDING_ROUTE_PATH = "/dashboard/onboarding";
export const ONBOARDING_HISTORY_ROUTE_PATH = "/dashboard/onboarding/history";
export const STRATEGY_ROUTE_PATH = "/dashboard/strategy";
export const PROJECTS_ROUTE_PATH = "/dashboard/projects";
export const GUIDE_ROUTE_PATH = "/dashboard/guide";
export const ADMIN_HOME_ROUTE_PATH = "/admin/operations";
export const DECISIONS_ROUTE_PATH = "/admin/decisions";
export const EXPERIMENTS_ROUTE_PATH = "/admin/experiments";
export const SYNTHETIC_ROUTE_PATH = "/admin/synthetic-backtest";
export const OPERATIONS_ROUTE_PATH = "/admin/operations";
export const ACCURACY_REPORT_ROUTE_PATH = "/admin/accuracy-report";
export const DECISION_SAMPLES_ROUTE_PATH = "/admin/decision-samples";

export function routeKeyFromPath(pathname: string): RouteKey {
  if (pathname.startsWith("/dashboard/bids")) return "bids";
  if (pathname.startsWith("/dashboard/opportunities")) return "opportunities";
  if (pathname.startsWith("/dashboard/results")) return "results";
  return "home";
}

/**
 * Active key for `BottomNav`. Returns `null` for paths that don't map to a
 * bottom-tab (e.g. `/dashboard/strategy`, `/dashboard/projects`), so no tab
 * is highlighted. `routeKeyFromPath` keeps a `"home"` fallback for other
 * consumers.
 */
export function bottomNavKeyForPath(pathname: string): RouteKey | null {
  if (pathname.startsWith("/admin")) return null;
  if (pathname.startsWith(GUIDE_ROUTE_PATH)) return null;
  if (pathname.startsWith(ONBOARDING_ROUTE_PATH)) return null;
  if (pathname.startsWith(PROFILE_ROUTE_PATH)) return null;
  if (pathname.startsWith(STRATEGY_ROUTE_PATH)) return null;
  if (pathname.startsWith(PROJECTS_ROUTE_PATH)) return null;
  if (pathname.startsWith(DECISIONS_ROUTE_PATH)) return null;
  if (pathname.startsWith(EXPERIMENTS_ROUTE_PATH)) return null;
  if (pathname.startsWith(SYNTHETIC_ROUTE_PATH)) return null;
  if (pathname.startsWith(OPERATIONS_ROUTE_PATH)) return null;
  if (pathname.startsWith(ACCURACY_REPORT_ROUTE_PATH)) return null;
  if (pathname.startsWith(DECISION_SAMPLES_ROUTE_PATH)) return null;
  return routeKeyFromPath(pathname);
}

export function pageTitleForPath(pathname: string): string {
  if (pathname.startsWith(GUIDE_ROUTE_PATH)) return "이용 가이드";
  if (pathname.startsWith(ONBOARDING_HISTORY_ROUTE_PATH)) return "온보딩 감사 이력";
  if (pathname.startsWith(ONBOARDING_ROUTE_PATH)) return "사업자 온보딩";
  if (pathname.startsWith(PROFILE_ROUTE_PATH)) return "업체 정보";
  if (pathname.startsWith(STRATEGY_ROUTE_PATH)) return "전략 편집";
  if (pathname.startsWith(PROJECTS_ROUTE_PATH)) return "공고 탐색";
  if (pathname.startsWith(DECISIONS_ROUTE_PATH)) return "결정 게이트웨이";
  if (pathname.startsWith(EXPERIMENTS_ROUTE_PATH)) return "실험 lifecycle";
  if (pathname.startsWith(SYNTHETIC_ROUTE_PATH)) return "가상 운영자 백테스트";
  if (pathname.startsWith(OPERATIONS_ROUTE_PATH)) return "운영 대시보드";
  if (pathname.startsWith(ACCURACY_REPORT_ROUTE_PATH)) return "정확도 리포트";
  if (pathname.startsWith(DECISION_SAMPLES_ROUTE_PATH)) return "의사결정 증적";
  const route = routeKeyFromPath(pathname);
  if (route === "home") return "오늘 할 일";
  return ROUTE_LABELS[route].label;
}
