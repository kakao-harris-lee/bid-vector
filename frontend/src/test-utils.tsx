import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppRoutes } from "@/app/router";
import { Toaster } from "@/shared/components/ui";

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
        <AppRoutes />
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
