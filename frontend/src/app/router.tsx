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
        <Route path="decisions" element={<Lazy><DecisionsScreen /></Lazy>} />
        <Route
          path="decisions/:id/summary"
          element={<Lazy><BidSummaryScreen /></Lazy>}
        />
        <Route path="accuracy-report" element={<Lazy><AccuracyReportScreen /></Lazy>} />
        <Route path="decision-samples" element={<Lazy><DecisionSamplesScreen /></Lazy>} />
        <Route path="experiments" element={<Lazy><ExperimentsScreen /></Lazy>} />
        <Route path="synthetic-backtest" element={<Lazy><ExperimentLabScreen /></Lazy>} />
        <Route path="operations" element={<Lazy><OperationsScreen /></Lazy>} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
