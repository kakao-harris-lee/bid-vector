import { Navigate, Route, Routes } from "react-router-dom";
import { AuthGate } from "./layout/AuthGate";
import { Shell } from "./layout/Shell";
import { HomeScreen } from "@/features/dashboard/HomeScreen";
import { OpportunitiesScreen } from "@/features/dashboard/OpportunitiesScreen";
import { BidsScreen } from "@/features/dashboard/BidsScreen";
import { ResultsScreen } from "@/features/dashboard/ResultsScreen";
import { StrategyEditor } from "@/features/strategy";
import { ProjectsScreen, ProjectDetailScreen } from "@/features/projects";
import { DecisionsScreen } from "@/features/decisions";
import { ExperimentsScreen } from "@/features/experiments";

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
        <Route path="opportunities" element={<OpportunitiesScreen />} />
        <Route path="bids" element={<BidsScreen />} />
        <Route path="results" element={<ResultsScreen />} />
        <Route path="strategy" element={<StrategyEditor />} />
        <Route path="projects" element={<ProjectsScreen />} />
        <Route path="projects/:id" element={<ProjectDetailScreen />} />
        <Route path="decisions" element={<DecisionsScreen />} />
        <Route path="experiments" element={<ExperimentsScreen />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
