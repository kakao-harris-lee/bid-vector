import { useState } from "react";
import { useShellContext } from "@/app/dashboardContext";
import {
  DetailDrawer,
  EmptyState,
  ItemList,
  LoadingState,
  SectionHeader
} from "./components";
import { useBidsQuery } from "./hooks";
import type { DetailSelection } from "./types";

export function BidsScreen() {
  const { summary, session, activeOperator } = useShellContext();
  const query = useBidsQuery(session, activeOperator.activeOperatorId);
  const [selected, setSelected] = useState<DetailSelection | null>(null);

  if (summary.isPending && !summary.data) return <LoadingState />;
  if (!summary.data) {
    return <EmptyState title="대시보드 데이터 없음" detail="표시할 데이터가 없습니다." />;
  }

  const items = query.data?.items.length ? query.data.items : summary.data.recent_bids;
  const loading = query.isFetching && !items.length;

  return (
    <>
      <section>
        <SectionHeader title="투찰" count={items.length} />
        {loading ? <LoadingState /> : <ItemList route="bids" items={items} onSelect={setSelected} />}
      </section>
      <DetailDrawer
        selection={selected}
        onClose={() => setSelected(null)}
        username={session?.username ?? null}
        authToken={session?.token ?? null}
      />
    </>
  );
}
