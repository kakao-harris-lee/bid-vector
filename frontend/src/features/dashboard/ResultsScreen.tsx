import { useState } from "react";
import { useShellContext } from "@/app/dashboardContext";
import {
  BacktestSummaryPanel,
  DetailDrawer,
  EmptyState,
  ItemList,
  LoadingState,
  SectionHeader
} from "./components";
import { usePaperSummaryQuery, useResultsQuery } from "./hooks";
import type { DetailSelection } from "./types";

export function ResultsScreen() {
  const { summary, session } = useShellContext();
  const results = useResultsQuery(session);
  const paper = usePaperSummaryQuery(session);
  const [selected, setSelected] = useState<DetailSelection | null>(null);

  if (summary.isPending && !summary.data) return <LoadingState />;
  if (!summary.data) {
    return <EmptyState title="대시보드 데이터 없음" detail="표시할 데이터가 없습니다." />;
  }

  const items = results.data?.items.length ? results.data.items : summary.data.recent_results;
  const loading = results.isFetching && !items.length;

  return (
    <>
      <section>
        <SectionHeader title="결과" count={items.length} />
        <BacktestSummaryPanel summary={paper.data} />
        {loading ? <LoadingState /> : <ItemList route="results" items={items} onSelect={setSelected} />}
      </section>
      <DetailDrawer
        selection={selected}
        onClose={() => setSelected(null)}
        username={session?.username ?? null}
      />
    </>
  );
}
