import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { UserRouteTree } from "@/app/router";
import { AdminRouteTree } from "@/app/router-admin";
import { Toaster } from "@/shared/components/ui";

/**
 * Production ships two separate Vite bundles (user `/dashboard`, admin
 * `/admin`), so `router.tsx` and `router-admin.tsx` each own only their own
 * route tree. Tests, however, exercise both surfaces through a single
 * `renderApp()` (admin specs push `/admin/*`, user specs push `/dashboard`),
 * so here we mount both bare route subtrees in one combined `<Routes>`.
 */
function CombinedRoutes() {
  return (
    <Routes>
      {UserRouteTree()}
      {AdminRouteTree()}
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0, refetchOnWindowFocus: false },
      mutations: { retry: false }
    }
  });
}

export function renderApp() {
  const queryClient = createTestQueryClient();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <CombinedRoutes />
        <Toaster />
      </BrowserRouter>
    </QueryClientProvider>
  );
  return { ...result, queryClient };
}

export function renderWithProviders(ui: ReactElement) {
  const queryClient = createTestQueryClient();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {ui}
        <Toaster />
      </BrowserRouter>
    </QueryClientProvider>
  );
  return { ...result, queryClient };
}
