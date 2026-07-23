import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthGate } from "./layout/AuthGate";
import { Shell } from "./layout/Shell";
import { HomeScreen } from "@/features/dashboard/HomeScreen";
import { OpportunitiesScreen } from "@/features/dashboard/OpportunitiesScreen";
import { BidsScreen } from "@/features/dashboard/BidsScreen";
import { ResultsScreen } from "@/features/dashboard/ResultsScreen";
import { GuideScreen } from "@/features/guide";

// Heavier secondary screens are code-split so the initial /dashboard bundle
// stays under the Vite 500 kB warning threshold once recharts + zod schemas
// load on first use.
const StrategyEditor = lazy(() =>
  import("@/features/strategy").then((mod) => ({ default: mod.StrategyEditor }))
);
const CompanyInfoEditor = lazy(() =>
  import("@/features/profile").then((mod) => ({ default: mod.CompanyInfoEditor }))
);
const OnboardingWizard = lazy(() =>
  import("@/features/onboarding").then((mod) => ({ default: mod.OnboardingWizard }))
);
const OnboardingHistoryScreen = lazy(() =>
  import("@/features/onboarding").then((mod) => ({ default: mod.OnboardingHistoryScreen }))
);
const ProjectsScreen = lazy(() =>
  import("@/features/projects").then((mod) => ({ default: mod.ProjectsScreen }))
);
const ProjectDetailScreen = lazy(() =>
  import("@/features/projects").then((mod) => ({ default: mod.ProjectDetailScreen }))
);
// BidSummaryScreen is the only user-facing screen under features/decisions/.
// Import the file directly (NOT the barrel) so the admin-only decision screens
// (DecisionsScreen/AccuracyReportScreen/DecisionSamplesScreen) never get pulled
// into the user (/dashboard) bundle.
const BidSummaryScreen = lazy(() =>
  import("@/features/decisions/BidSummaryScreen").then((mod) => ({
    default: mod.BidSummaryScreen
  }))
);

function LazyFallback() {
  return <p className="p-4 text-sm text-[var(--color-muted)]">로딩 중…</p>;
}

function Lazy({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<LazyFallback />}>{children}</Suspense>;
}

/**
 * Bare user route subtree (no `<Routes>` wrapper, no catch-all).
 *
 * `AppRoutes` wraps this in `<Routes>` for the production user app. Tests mount
 * it alongside the admin subtree in a single combined `<Routes>` (see
 * `test-utils.tsx`) so existing specs that exercise both surfaces keep working.
 */
export function UserRouteTree() {
  return (
    <>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/dashboard"
        element={
          <AuthGate>
            <Shell surface="user" />
          </AuthGate>
        }
      >
        <Route index element={<HomeScreen />} />
        <Route path="guide" element={<GuideScreen />} />
        <Route path="opportunities" element={<OpportunitiesScreen />} />
        <Route path="bids" element={<BidsScreen />} />
        <Route path="results" element={<ResultsScreen />} />
        <Route path="strategy" element={<Lazy><StrategyEditor /></Lazy>} />
        <Route path="profile" element={<Lazy><CompanyInfoEditor /></Lazy>} />
        <Route path="onboarding" element={<Lazy><OnboardingWizard /></Lazy>} />
        <Route path="onboarding/history" element={<Lazy><OnboardingHistoryScreen /></Lazy>} />
        <Route path="projects" element={<Lazy><ProjectsScreen /></Lazy>} />
        <Route path="projects/:id" element={<Lazy><ProjectDetailScreen /></Lazy>} />
        <Route
          path="decisions/:id/summary"
          element={<Lazy><BidSummaryScreen /></Lazy>}
        />
      </Route>
    </>
  );
}

/**
 * Production user app routes. The admin surface is served as a separate Vite
 * bundle (see `router-admin.tsx`), so any non-user path falls back to
 * `/dashboard` rather than redirecting into `/admin`.
 */
export function AppRoutes() {
  return (
    <Routes>
      {UserRouteTree()}
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
