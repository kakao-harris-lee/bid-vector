import type { PropsWithChildren } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        refetchOnWindowFocus: false,
        staleTime: 15_000
      },
      mutations: {
        retry: 0
      }
    }
  });
}

export interface AppProvidersProps extends PropsWithChildren {
  queryClient?: QueryClient;
}

export function AppProviders({ children, queryClient }: AppProvidersProps) {
  const client = queryClient ?? createQueryClient();
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
