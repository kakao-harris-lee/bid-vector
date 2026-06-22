import { lazy, Suspense, useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthGate, useAuthSession } from "./layout/AuthGate";
import { Shell } from "./layout/Shell";
import { useActiveOperator } from "./operatorContext";
import { isStandaloneBundle } from "@/shared/crossAppNav";

// The user app lives at /dashboard. When the admin bundle is served standalone,
// bouncing a non-privileged operator there is a cross-app boundary, so it must
// be a full-page navigation — an in-app <Navigate> would hit the admin
// catch-all and loop. In the combined test routes (base "/") both surfaces
// share one router, so the in-app <Navigate> is correct. See
// `@/shared/crossAppNav`.
const USER_APP_PATH = "/dashboard";

// Admin screens are code-split. Each is imported from its individual file
// (NOT the features/decisions barrel) so the admin bundle pulls in exactly the
// admin decision screens — and the user bundle never sees them.
const OperationsScreen = lazy(() =>
  import("@/features/operations").then((mod) => ({ default: mod.OperationsScreen }))
);
const ExperimentLabScreen = lazy(() =>
  import("@/features/synthetic-backtest").then((mod) => ({
    default: mod.ExperimentLabScreen
  }))
);
const ExperimentsScreen = lazy(() =>
  import("@/features/experiments").then((mod) => ({ default: mod.ExperimentsScreen }))
);
const DecisionsScreen = lazy(() =>
  import("@/features/decisions/DecisionsScreen").then((mod) => ({
    default: mod.DecisionsScreen
  }))
);
const AccuracyReportScreen = lazy(() =>
  import("@/features/decisions/AccuracyReportScreen").then((mod) => ({
    default: mod.AccuracyReportScreen
  }))
);
const DecisionSamplesScreen = lazy(() =>
  import("@/features/decisions/DecisionSamplesScreen").then((mod) => ({
    default: mod.DecisionSamplesScreen
  }))
);

function LazyFallback() {
  return <p className="p-4 text-sm text-[var(--color-muted)]">로딩 중…</p>;
}

function Lazy({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<LazyFallback />}>{children}</Suspense>;
}

/**
 * Sends a non-privileged operator back to the user app. In the standalone admin
 * bundle this is a cross-app full-page navigation; in the combined test app it
 * falls back to an in-app <Navigate> (the user routes are mounted alongside).
 */
function RedirectToUserApp() {
  const standalone = isStandaloneBundle();

  useEffect(() => {
    if (standalone) {
      window.location.assign(USER_APP_PATH);
    }
  }, [standalone]);

  if (standalone) {
    return <LazyFallback />;
  }
  return <Navigate to={USER_APP_PATH} replace />;
}

/**
 * Gate the admin shell behind operator privilege. Non-privileged operators are
 * bounced back to the user app (`/dashboard`); see `RedirectToUserApp`.
 */
function AdminShellGuard() {
  const session = useAuthSession();
  const activeOperator = useActiveOperator(session);

  if (activeOperator.isLoading) {
    return <LazyFallback />;
  }

  if (!activeOperator.isPrivileged) {
    return <RedirectToUserApp />;
  }

  return <Shell surface="admin" />;
}

/**
 * Bare admin route subtree (no `<Routes>` wrapper, no catch-all). `AdminRoutes`
 * wraps this for the production admin app; tests mount it alongside the user
 * subtree in a single combined `<Routes>` (see `test-utils.tsx`).
 */
export function AdminRouteTree() {
  return (
    <Route
      path="/admin"
      element={
        <AuthGate>
          <AdminShellGuard />
        </AuthGate>
      }
    >
      <Route index element={<Navigate to="/admin/operations" replace />} />
      <Route path="operations" element={<Lazy><OperationsScreen /></Lazy>} />
      <Route path="synthetic-backtest" element={<Lazy><ExperimentLabScreen /></Lazy>} />
      <Route path="experiments" element={<Lazy><ExperimentsScreen /></Lazy>} />
      <Route path="accuracy-report" element={<Lazy><AccuracyReportScreen /></Lazy>} />
      <Route path="decision-samples" element={<Lazy><DecisionSamplesScreen /></Lazy>} />
      <Route path="decisions" element={<Lazy><DecisionsScreen /></Lazy>} />
    </Route>
  );
}

/**
 * Production admin app routes. Served as a standalone bundle at `/admin`. Any
 * non-admin path redirects to `/admin/operations` (the admin home); the user
 * surface lives in a separate bundle at `/dashboard`.
 */
export function AdminRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/admin/operations" replace />} />
      {AdminRouteTree()}
      <Route path="*" element={<Navigate to="/admin/operations" replace />} />
    </Routes>
  );
}
