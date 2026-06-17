import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthGate, useAuthSession } from "./layout/AuthGate";
import { Shell } from "./layout/Shell";
import { useActiveOperator } from "./operatorContext";
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
const ProjectsScreen = lazy(() =>
  import("@/features/projects").then((mod) => ({ default: mod.ProjectsScreen }))
);
const ProjectDetailScreen = lazy(() =>
  import("@/features/projects").then((mod) => ({ default: mod.ProjectDetailScreen }))
);
const DecisionsScreen = lazy(() =>
  import("@/features/decisions").then((mod) => ({ default: mod.DecisionsScreen }))
);
const BidSummaryScreen = lazy(() =>
  import("@/features/decisions").then((mod) => ({ default: mod.BidSummaryScreen }))
);
const AccuracyReportScreen = lazy(() =>
  import("@/features/decisions").then((mod) => ({ default: mod.AccuracyReportScreen }))
);
const DecisionSamplesScreen = lazy(() =>
  import("@/features/decisions").then((mod) => ({ default: mod.DecisionSamplesScreen }))
);
const ExperimentsScreen = lazy(() =>
  import("@/features/experiments").then((mod) => ({ default: mod.ExperimentsScreen }))
);
const ExperimentLabScreen = lazy(() =>
  import("@/features/synthetic-backtest").then((mod) => ({ default: mod.ExperimentLabScreen }))
);
const OperationsScreen = lazy(() =>
  import("@/features/operations").then((mod) => ({ default: mod.OperationsScreen }))
);

function LazyFallback() {
  return <p className="p-4 text-sm text-[var(--color-muted)]">로딩 중…</p>;
}

function Lazy({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<LazyFallback />}>{children}</Suspense>;
}

function AdminShellGuard() {
  const session = useAuthSession();
  const activeOperator = useActiveOperator(session);

  if (activeOperator.isLoading) {
    return <LazyFallback />;
  }

  if (!activeOperator.isPrivileged) {
    return <Navigate to="/dashboard" replace />;
  }

  return <Shell surface="admin" />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/dashboard"
        element={
          <AuthGate>
            <Shell />
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
        <Route path="projects" element={<Lazy><ProjectsScreen /></Lazy>} />
        <Route path="projects/:id" element={<Lazy><ProjectDetailScreen /></Lazy>} />
        <Route path="decisions" element={<Navigate to="/admin/decisions" replace />} />
        <Route
          path="decisions/:id/summary"
          element={<Lazy><BidSummaryScreen /></Lazy>}
        />
        <Route path="accuracy-report" element={<Navigate to="/admin/accuracy-report" replace />} />
        <Route path="decision-samples" element={<Navigate to="/admin/decision-samples" replace />} />
        <Route path="experiments" element={<Navigate to="/admin/experiments" replace />} />
        <Route path="synthetic-backtest" element={<Navigate to="/admin/synthetic-backtest" replace />} />
        <Route path="operations" element={<Navigate to="/admin/operations" replace />} />
      </Route>
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
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
