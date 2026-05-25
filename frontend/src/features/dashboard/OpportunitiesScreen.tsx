import { useMemo, useState } from "react";
import { useShellContext } from "@/app/dashboardContext";
import {
  filterOpportunities,
  sortOpportunities,
  type OpportunityFilter,
  type OpportunitySort
} from "@/shared/lib";
import {
  DetailDrawer,
  EmptyState,
  ItemList,
  LoadingState,
  SectionHeader
} from "./components";
import { useOpportunitiesQuery } from "./hooks";
import type { DetailSelection } from "./types";

export function OpportunitiesScreen() {
  const { summary, session } = useShellContext();
  const query = useOpportunitiesQuery(session);
  const [filter, setFilter] = useState<OpportunityFilter>("all");
  const [sort, setSort] = useState<OpportunitySort>("deadline");
  const [selected, setSelected] = useState<DetailSelection | null>(null);

  const sourceItems = query.data?.items.length
    ? query.data.items
    : (summary.data?.recent_opportunities ?? []);
  const items = useMemo(
    () => sortOpportunities(filterOpportunities(sourceItems, filter), sort),
    [filter, sort, sourceItems]
  );

  if (summary.isPending && !summary.data) return <LoadingState />;
  if (!summary.data) {
    return <EmptyState title="대시보드 데이터 없음" detail="표시할 데이터가 없습니다." />;
  }

  const loading = query.isFetching && !sourceItems.length;

  return (
    <>
      <section>
        <SectionHeader title="입찰" count={items.length} />
        <OpportunityToolbar
          filter={filter}
          sort={sort}
          onFilterChange={setFilter}
          onSortChange={setSort}
        />
        {loading ? (
          <LoadingState />
        ) : (
          <ItemList route="opportunities" items={items} onSelect={setSelected} />
        )}
      </section>
      <DetailDrawer
        selection={selected}
        onClose={() => setSelected(null)}
        username={session?.username ?? null}
      />
    </>
  );
}

function OpportunityToolbar({
  filter,
  sort,
  onFilterChange,
  onSortChange
}: {
  filter: OpportunityFilter;
  sort: OpportunitySort;
  onFilterChange: (filter: OpportunityFilter) => void;
  onSortChange: (sort: OpportunitySort) => void;
}) {
  const filters: Array<{ key: OpportunityFilter; label: string }> = [
    { key: "all", label: "전체" },
    { key: "overdue", label: "마감 지남" },
    { key: "due_today", label: "오늘 마감" },
    { key: "paper", label: "페이퍼" },
    { key: "decision", label: "판단" }
  ];

  return (
    <div className="list-toolbar">
      <div className="filter-chips" aria-label="입찰 후보 필터">
        {filters.map((item) => (
          <button
            key={item.key}
            className={filter === item.key ? "active" : ""}
            type="button"
            onClick={() => onFilterChange(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <label className="sort-select">
        정렬
        <select value={sort} onChange={(event) => onSortChange(event.target.value as OpportunitySort)}>
          <option value="deadline">마감순</option>
          <option value="priority">점수순</option>
          <option value="amount">금액순</option>
        </select>
      </label>
    </div>
  );
}
