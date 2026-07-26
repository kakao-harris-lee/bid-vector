import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useShellContext } from "@/app/dashboardContext";
import {
  fetchSyntheticOperators,
  runSyntheticBacktest,
  seedSyntheticOperators
} from "@/shared/api";
import { Button, Card, CardContent, CardHeader, CardTitle, Input, toastApi } from "@/shared/components/ui";
import type {
  SyntheticBacktestOperatorResult,
  SyntheticBacktestRunRequest,
  SyntheticBacktestRunResponse
} from "@/shared/types/synthetic";
import {
  ArchetypeDrilldown,
  ComparisonTable,
  SeedPanel,
  WinRateBarChart,
  type SortKey
} from "./components";

export function SyntheticBacktestScreen() {
  const { session } = useShellContext();
  const queryClient = useQueryClient();

  const operators = useQuery({
    queryKey: ["synthetic", "operators"],
    queryFn: () => fetchSyntheticOperators(session?.token),
    enabled: Boolean(session?.token)
  });

  const seedMutation = useMutation({
    mutationFn: (purge: boolean) => seedSyntheticOperators({ purge }, session?.token),
    onSuccess: (data, purge) => {
      void queryClient.invalidateQueries({ queryKey: ["synthetic", "operators"] });
      toastApi.success({
        title: purge ? "리시드 완료" : "시드 완료",
        description: `12개 아키타입 중 ${data.seeded_count}건 upsert · ${data.purged_count}건 정리`
      });
    },
    onError: (err) =>
      toastApi.danger({
        title: "시드 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const [scenario, setScenario] = useState("base");
  const [limit, setLimit] = useState(100);
  const [category, setCategory] = useState("");

  const runMutation = useMutation<SyntheticBacktestRunResponse, Error, SyntheticBacktestRunRequest>({
    mutationFn: (payload) => runSyntheticBacktest(payload, session?.token),
    onError: (err) =>
      toastApi.danger({
        title: "백테스트 실행 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const [sortKey, setSortKey] = useState<SortKey>("win_rate_on_settled");
  const [drilldown, setDrilldown] = useState<SyntheticBacktestOperatorResult | null>(null);
  const sortedResults = useMemo(() => {
    const items = runMutation.data?.results ?? [];
    return [...items].sort((a, b) => compareNullable(b[sortKey], a[sortKey]));
  }, [runMutation.data, sortKey]);

  return (
    <section className="flex flex-col gap-4" aria-label="synthetic 백테스트 비교">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">가상 운영자 백테스트</h2>
        <span className="text-xs text-[var(--color-muted)]">
          win_rate는 가격 기준 추정 낙찰(would_have_won_price_only_count / settled_count). 실제 낙찰이 아님.
        </span>
      </header>

      <SeedPanel
        operators={operators.data?.operators ?? []}
        loading={operators.isPending}
        onSeed={(purge) => seedMutation.mutate(purge)}
        seedPending={seedMutation.isPending}
      />

      <Card>
        <CardHeader>
          <CardTitle>백테스트 실행</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-4">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-[var(--color-muted)]">scenario</span>
            <Input value={scenario} onChange={(event) => setScenario(event.target.value)} aria-label="scenario" />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-[var(--color-muted)]">limit</span>
            <Input
              type="number"
              min={1}
              max={1000}
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value) || 1)}
              aria-label="limit"
              className="tabular-nums"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-[var(--color-muted)]">category (선택)</span>
            <Input
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              aria-label="category"
              placeholder="software ..."
            />
          </label>
          <div className="flex items-end">
            <Button
              type="button"
              onClick={() =>
                runMutation.mutate({
                  scenario,
                  limit,
                  category: category || undefined
                })
              }
              disabled={runMutation.isPending || (operators.data?.operator_count ?? 0) === 0}
            >
              {runMutation.isPending ? "실행 중" : "실행"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {runMutation.data ? (
        <>
          <WinRateBarChart results={sortedResults} />
          <ComparisonTable
            results={sortedResults}
            sortKey={sortKey}
            onSortKeyChange={setSortKey}
            onSelect={setDrilldown}
          />
        </>
      ) : null}

      {drilldown ? (
        <ArchetypeDrilldown row={drilldown} onClose={() => setDrilldown(null)} />
      ) : null}
    </section>
  );
}

function compareNullable(a: number | null | undefined, b: number | null | undefined): number {
  const aIsNull = a === null || a === undefined;
  const bIsNull = b === null || b === undefined;
  if (aIsNull && bIsNull) return 0;
  if (aIsNull) return 1;
  if (bIsNull) return -1;
  return a - b;
}
