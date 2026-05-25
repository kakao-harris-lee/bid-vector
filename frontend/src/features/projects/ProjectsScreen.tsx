import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Search } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input } from "@/shared/components/ui";
import { useDebouncedValue } from "@/shared/hooks";
import { formatCurrencyCompact, formatDateTime } from "@/shared/lib";
import type { ProjectListQuery } from "@/shared/api";
import type { ProjectResponse, ProjectStatus } from "@/shared/types";
import { useProjectsQuery } from "./hooks";

const PAGE_SIZE = 20;

const STATUS_LABELS: Record<string, string> = {
  open: "공고 중",
  re_notice: "재공고",
  closed: "마감",
  awarded: "낙찰",
  failed: "유찰",
  cancelled: "취소"
};

const STATUS_TONE: Record<string, "info" | "healthy" | "watch" | "critical" | "muted"> = {
  open: "info",
  re_notice: "watch",
  closed: "muted",
  awarded: "healthy",
  failed: "critical",
  cancelled: "muted"
};

export function ProjectsScreen() {
  const { session } = useShellContext();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Form-level state mirrors the URL but updates locally as the user types so
  // each keystroke does not push a history entry; the debounced version then
  // syncs to the URL + query.
  const initial = useMemo(() => readFromSearch(searchParams), []);
  const [draft, setDraft] = useState(initial);
  const debounced = useDebouncedValue(draft, 300);

  useEffect(() => {
    const next = serializeToSearch(debounced);
    if (next !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const query: ProjectListQuery = {
    q: debounced.q || undefined,
    category: debounced.category || undefined,
    status: debounced.status || undefined,
    agency: debounced.agency || undefined,
    budgetMin: parseNumber(debounced.budgetMin),
    budgetMax: parseNumber(debounced.budgetMax),
    skip: debounced.skip,
    limit: PAGE_SIZE
  };

  const list = useProjectsQuery(session, query);

  const total = list.data?.total ?? 0;
  const items = list.data?.items ?? [];
  const skip = debounced.skip;
  const showingFrom = total === 0 ? 0 : skip + 1;
  const showingTo = Math.min(skip + items.length, total);
  const canPrev = skip > 0;
  const canNext = skip + items.length < total;

  return (
    <section aria-label="공고 탐색" className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">공고 탐색</h2>
        <span className="text-xs text-[var(--color-muted)]">
          제목·기관·예산 필터는 URL에 반영되어 북마크할 수 있습니다.
        </span>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>필터</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <FilterField label="제목/공고번호">
            <div className="relative">
              <Search
                size={14}
                className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[var(--color-muted)]"
                aria-hidden="true"
              />
              <Input
                value={draft.q}
                onChange={(event) => updateDraft({ q: event.target.value })}
                placeholder="예) 공항, R26BK..."
                aria-label="제목 또는 공고번호 검색"
                className="pl-7"
              />
            </div>
          </FilterField>
          <FilterField label="카테고리">
            <Input
              value={draft.category}
              onChange={(event) => updateDraft({ category: event.target.value })}
              placeholder="software, construction ..."
              aria-label="카테고리"
            />
          </FilterField>
          <FilterField label="상태">
            <select
              value={draft.status}
              onChange={(event) => updateDraft({ status: event.target.value })}
              aria-label="공고 상태"
              className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-sm"
            >
              <option value="">전체</option>
              {Object.entries(STATUS_LABELS).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </FilterField>
          <FilterField label="기관 (issuing/demand)">
            <Input
              value={draft.agency}
              onChange={(event) => updateDraft({ agency: event.target.value })}
              placeholder="조달청, 서울시 ..."
              aria-label="기관 검색"
            />
          </FilterField>
          <FilterField label="최소 예산 (원)">
            <Input
              type="number"
              min={0}
              step={1_000_000}
              value={draft.budgetMin}
              onChange={(event) => updateDraft({ budgetMin: event.target.value })}
              placeholder="0"
              aria-label="최소 예산"
              className="tabular-nums"
            />
          </FilterField>
          <FilterField label="최대 예산 (원)">
            <Input
              type="number"
              min={0}
              step={1_000_000}
              value={draft.budgetMax}
              onChange={(event) => updateDraft({ budgetMax: event.target.value })}
              placeholder="무제한"
              aria-label="최대 예산"
              className="tabular-nums"
            />
          </FilterField>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-muted)]">
        <span>
          총 {total.toLocaleString("ko-KR")}건
          {items.length > 0 ? ` · ${showingFrom}–${showingTo} 표시` : ""}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            const reset = emptyDraft();
            setDraft(reset);
            setSearchParams(new URLSearchParams(), { replace: true });
          }}
          disabled={isDraftEmpty(draft)}
        >
          필터 초기화
        </Button>
      </div>

      {list.error ? (
        <p className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]" role="alert">
          {list.error.message ?? "공고 목록을 불러오지 못했습니다."}
        </p>
      ) : null}

      {list.isPending && items.length === 0 ? (
        <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
      ) : items.length === 0 ? (
        <p className="rounded-md border border-dashed border-[var(--color-border)] px-3 py-6 text-center text-sm text-[var(--color-muted)]">
          조건에 맞는 공고가 없습니다. 필터를 완화해 보세요.
        </p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label="공고 목록">
          {items.map((project) => (
            <ProjectRow
              key={project.id}
              project={project}
              onSelect={() => navigate(`/dashboard/projects/${project.id}`)}
            />
          ))}
        </ul>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => updateDraft({ skip: Math.max(0, skip - PAGE_SIZE) })}
          disabled={!canPrev || list.isFetching}
        >
          이전
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={() => updateDraft({ skip: skip + PAGE_SIZE })}
          disabled={!canNext || list.isFetching}
        >
          다음
        </Button>
      </div>
    </section>
  );

  function updateDraft(partial: Partial<DraftQuery>) {
    setDraft((prev) => {
      // any meaningful filter change resets pagination to first page
      const resettingFilters = Object.keys(partial).some((key) => key !== "skip");
      return {
        ...prev,
        ...partial,
        skip: resettingFilters ? 0 : (partial.skip ?? prev.skip)
      };
    });
  }
}

function ProjectRow({
  project,
  onSelect
}: {
  project: ProjectResponse;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className="flex w-full flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3 text-left transition-colors hover:border-[var(--color-primary)]"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-medium text-[var(--color-fg)]" title={project.title}>
            {project.title}
          </span>
          <Badge tone={STATUS_TONE[project.status as ProjectStatus] ?? "info"}>
            {STATUS_LABELS[project.status] ?? project.status}
          </Badge>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-muted)]">
          <span>
            {project.category}
            {project.issuing_agency ? ` · ${project.issuing_agency}` : ""}
            {project.notice_number ? ` · ${project.notice_number}` : ""}
          </span>
          <span className="tabular-nums">
            {formatCurrencyCompact(project.budget_estimate)} · {formatDateTime(project.created_at)}
          </span>
        </div>
      </button>
    </li>
  );
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-[var(--color-muted)]">{label}</span>
      {children}
    </label>
  );
}

interface DraftQuery {
  q: string;
  category: string;
  status: string;
  agency: string;
  budgetMin: string;
  budgetMax: string;
  skip: number;
}

function emptyDraft(): DraftQuery {
  return { q: "", category: "", status: "", agency: "", budgetMin: "", budgetMax: "", skip: 0 };
}

function isDraftEmpty(draft: DraftQuery): boolean {
  return (
    !draft.q &&
    !draft.category &&
    !draft.status &&
    !draft.agency &&
    !draft.budgetMin &&
    !draft.budgetMax &&
    draft.skip === 0
  );
}

function parseNumber(value: string): number | undefined {
  if (!value) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function readFromSearch(params: URLSearchParams): DraftQuery {
  return {
    q: params.get("q") ?? "",
    category: params.get("category") ?? "",
    status: params.get("status") ?? "",
    agency: params.get("agency") ?? "",
    budgetMin: params.get("budget_min") ?? "",
    budgetMax: params.get("budget_max") ?? "",
    skip: Number.parseInt(params.get("skip") ?? "0", 10) || 0
  };
}

function serializeToSearch(draft: DraftQuery): string {
  const params = new URLSearchParams();
  if (draft.q) params.set("q", draft.q);
  if (draft.category) params.set("category", draft.category);
  if (draft.status) params.set("status", draft.status);
  if (draft.agency) params.set("agency", draft.agency);
  if (draft.budgetMin) params.set("budget_min", draft.budgetMin);
  if (draft.budgetMax) params.set("budget_max", draft.budgetMax);
  if (draft.skip > 0) params.set("skip", String(draft.skip));
  return params.toString();
}
