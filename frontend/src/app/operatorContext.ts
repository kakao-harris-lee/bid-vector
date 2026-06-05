import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  fetchOperatorAccounts,
  queryKeys,
  type OperatorAccountListResponse
} from "@/shared/api";
import type { AuthSession } from "./layout/AuthGate";

/**
 * localStorage key for the operator the user is currently "viewing as".
 *
 * - `null` (or absent) → fall back to the token owner. Dashboards request
 *   without the `operator_id=` query param and the backend uses the canonical
 *   token-owner branch.
 * - A number → privileged caller picked a specific company (canonical or
 *   `synthetic-*`). The dashboards re-key under that operatorId and pass it
 *   in the URL.
 *
 * Non-privileged callers ignore any persisted value (the backend would 403
 * anyway, but we also reset client-side to keep the UI honest).
 */
export const ACTIVE_OPERATOR_STORAGE_KEY = "bid-vector.activeOperatorId";

const ACTIVE_OPERATOR_EVENT = "bid-vector:active-operator-change";

function readPersistedOperatorId(): number | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(ACTIVE_OPERATOR_STORAGE_KEY);
  if (!raw) return null;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed <= 0) {
    window.localStorage.removeItem(ACTIVE_OPERATOR_STORAGE_KEY);
    return null;
  }
  return parsed;
}

function persistOperatorId(operatorId: number | null): void {
  if (typeof window === "undefined") return;
  if (operatorId === null) {
    window.localStorage.removeItem(ACTIVE_OPERATOR_STORAGE_KEY);
  } else {
    window.localStorage.setItem(ACTIVE_OPERATOR_STORAGE_KEY, String(operatorId));
  }
  window.dispatchEvent(new Event(ACTIVE_OPERATOR_EVENT));
}

/**
 * Subscribe to operator-id changes. We mirror the same pattern as AuthGate —
 * dispatch a custom event so any hook on the page picks up the new value
 * without a full reload.
 */
function useActiveOperatorIdState(): [number | null, (next: number | null) => void] {
  const [operatorId, setOperatorId] = useState<number | null>(() =>
    readPersistedOperatorId()
  );

  useEffect(() => {
    const sync = () => setOperatorId(readPersistedOperatorId());
    window.addEventListener(ACTIVE_OPERATOR_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(ACTIVE_OPERATOR_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const setActiveOperatorId = useCallback((next: number | null) => {
    persistOperatorId(next);
    // Optimistic local update — the event listener above will also fire, but
    // setting it immediately avoids a render flicker for the caller component.
    setOperatorId(next);
  }, []);

  return [operatorId, setActiveOperatorId];
}

export type OperatorAccountRow = NonNullable<
  OperatorAccountListResponse["operators"]
>[number];

export interface ActiveOperator {
  /** `null` means "use the token owner's own data" (no query param sent). */
  activeOperatorId: number | null;
  /** Backend-provided fallback so chips can show "현재: <company>". */
  currentOperatorId: number | null;
  isPrivileged: boolean;
  /** Operator rows visible to the caller. Empty array until accounts load. */
  operators: OperatorAccountRow[];
  /** Current selection resolved against `operators[]` (or `null` when unknown). */
  currentOperator: OperatorAccountRow | null;
  isLoading: boolean;
  isError: boolean;
  setActiveOperatorId: (next: number | null) => void;
}

/**
 * Hook used by header + screens to read & mutate the active operator. Wires
 * react-query so a `setActiveOperatorId(...)` invalidates every dashboard /
 * analytics query so they refetch with the new `operator_id=`.
 */
export function useActiveOperator(session: AuthSession | null): ActiveOperator {
  const [storedOperatorId, setStoredOperatorId] = useActiveOperatorIdState();
  const queryClient = useQueryClient();

  const accountsQuery = useQuery<OperatorAccountListResponse, Error>({
    queryKey: queryKeys.operator.accounts(),
    queryFn: () => fetchOperatorAccounts(session?.token),
    enabled: Boolean(session?.token),
    staleTime: 5 * 60_000
  });

  const isPrivileged = accountsQuery.data?.is_privileged ?? false;
  const operators: OperatorAccountRow[] = accountsQuery.data?.operators ?? [];
  const currentOperatorId = accountsQuery.data?.current_operator_id ?? null;

  // Non-privileged callers cannot impersonate. Reset any stale value so the
  // dashboards revert to the token-owner branch and avoid 403s on refresh.
  useEffect(() => {
    if (!accountsQuery.data) return;
    if (accountsQuery.data.is_privileged) return;
    if (storedOperatorId !== null) {
      setStoredOperatorId(null);
    }
  }, [accountsQuery.data, storedOperatorId, setStoredOperatorId]);

  // If the persisted operatorId is not in the visible catalogue (e.g. account
  // disabled), drop it back to null so the UI doesn't silently 403.
  useEffect(() => {
    if (!accountsQuery.data) return;
    if (storedOperatorId === null) return;
    const known = operators.some((row) => row.operator_id === storedOperatorId);
    if (!known) {
      setStoredOperatorId(null);
    }
  }, [accountsQuery.data, operators, storedOperatorId, setStoredOperatorId]);

  const setActiveOperatorId = useCallback(
    (next: number | null) => {
      const normalized = next === currentOperatorId ? null : next;
      setStoredOperatorId(normalized);
      // Invalidate every operator-scoped cache prefix so screens re-fetch
      // under the new operatorId. We rely on prefix matching — every dashboard
      // queryKey starts with one of these tuples and React Query treats them
      // as broadcast invalidations.
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
    },
    [currentOperatorId, queryClient, setStoredOperatorId]
  );

  const currentOperator = useMemo(() => {
    const id = storedOperatorId ?? currentOperatorId;
    if (id === null) return null;
    return operators.find((row) => row.operator_id === id) ?? null;
  }, [operators, storedOperatorId, currentOperatorId]);

  return {
    activeOperatorId: storedOperatorId,
    currentOperatorId,
    isPrivileged,
    operators,
    currentOperator,
    isLoading: accountsQuery.isPending,
    isError: accountsQuery.isError && !(accountsQuery.error instanceof ApiError && accountsQuery.error.status === 401),
    setActiveOperatorId
  };
}
