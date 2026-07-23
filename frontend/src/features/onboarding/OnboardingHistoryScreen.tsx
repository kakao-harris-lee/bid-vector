import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { ONBOARDING_ROUTE_PATH } from "@/app/layout/Shell";
import { Badge, Button, Card, CardContent } from "@/shared/components/ui";
import { formatDateTime, formatPercent } from "@/shared/lib";
import type {
  OnboardingDecisionStatus,
  OnboardingSuggestionHistoryItem
} from "@/shared/api";
import {
  AUDIT_STATUS_ORDER,
  auditStatusMeta,
  FIELD_META,
  fieldMeta,
  sourceLabel
} from "./constants";
import { SuggestionValueDisplay } from "./SuggestionValueEditor";
import { useOnboardingHistory } from "./hooks";

const PAGE_SIZE = 20;

/** 필드 필터 select 옵션(선언적, §4.5-1) — FIELD_META 단일 출처를 재사용한다. */
const FIELD_OPTIONS = Object.entries(FIELD_META).map(([value, meta]) => ({
  value,
  label: meta.label
}));

/**
 * 온보딩 결정 감사 이력 화면(읽기 전용). apply 가 남긴 append-only 로그를 최신순으로
 * 보여준다 — 필드/상태 필터 + limit/offset 페이지네이션. 값은 canonical raw 로 표시하되
 * codeValued 필드만 표시 라벨(VALUE_LABELS)로 매핑한다(§8 ko 단일 번들, 표시 전용).
 */
export function OnboardingHistoryScreen() {
  const { session } = useShellContext();
  const navigate = useNavigate();
  const [status, setStatus] = useState<OnboardingDecisionStatus | "">("");
  const [field, setField] = useState("");
  const [offset, setOffset] = useState(0);

  const history = useOnboardingHistory(session, {
    field: field || undefined,
    status: status || undefined,
    limit: PAGE_SIZE,
    offset
  });

  const items = history.data?.items ?? [];
  const total = history.data?.total ?? 0;
  const showingFrom = total === 0 ? 0 : offset + 1;
  const showingTo = Math.min(offset + items.length, total);
  const canPrev = offset > 0;
  const canNext = offset + items.length < total;

  // 필터가 바뀌면 첫 페이지로 되돌린다.
  const changeStatus = (next: OnboardingDecisionStatus | "") => {
    setStatus(next);
    setOffset(0);
  };
  const changeField = (next: string) => {
    setField(next);
    setOffset(0);
  };

  return (
    <section aria-label="온보딩 감사 이력" className="flex flex-col gap-4">
      <header className="flex flex-col gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="self-start"
          onClick={() => navigate(ONBOARDING_ROUTE_PATH)}
        >
          <ArrowLeft size={15} aria-hidden="true" /> 온보딩으로
        </Button>
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold text-[var(--color-fg)]">온보딩 감사 이력</h2>
          <span className="text-xs text-[var(--color-muted)]">
            확정·수정·거부한 온보딩 결정 기록입니다. 최신 결정이 위에 표시됩니다.
          </span>
        </div>
      </header>

      <Card>
        <CardContent className="grid grid-cols-1 gap-3 pt-4 sm:grid-cols-2">
          <FilterField label="결정 상태">
            <select
              value={status}
              onChange={(event) => changeStatus(event.target.value as OnboardingDecisionStatus | "")}
              aria-label="결정 상태 필터"
              className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-sm"
            >
              <option value="">전체</option>
              {AUDIT_STATUS_ORDER.map((key) => (
                <option key={key} value={key}>
                  {auditStatusMeta(key).label}
                </option>
              ))}
            </select>
          </FilterField>
          <FilterField label="필드">
            <select
              value={field}
              onChange={(event) => changeField(event.target.value)}
              aria-label="필드 필터"
              className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-sm"
            >
              <option value="">전체</option>
              {FIELD_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </FilterField>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-muted)]">
        <span>
          총 {total.toLocaleString("ko-KR")}건
          {items.length > 0 ? ` · ${showingFrom}–${showingTo} 표시` : ""}
        </span>
        {(status || field) && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setStatus("");
              setField("");
              setOffset(0);
            }}
          >
            필터 초기화
          </Button>
        )}
      </div>

      {history.error ? (
        <p
          role="alert"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          {history.error.message ?? "온보딩 감사 이력을 불러오지 못했습니다."}
        </p>
      ) : history.isPending && items.length === 0 ? (
        <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
      ) : items.length === 0 ? (
        <p className="rounded-md border border-dashed border-[var(--color-border)] px-3 py-6 text-center text-sm text-[var(--color-muted)]">
          아직 감사 이력이 없습니다. 온보딩에서 후보를 확정하면 여기에 기록됩니다.
        </p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label="감사 이력 목록">
          {items.map((item) => (
            <HistoryRow key={item.id} item={item} />
          ))}
        </ul>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setOffset((prev) => Math.max(0, prev - PAGE_SIZE))}
          disabled={!canPrev || history.isFetching}
        >
          이전
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
          disabled={!canNext || history.isFetching}
        >
          다음
        </Button>
      </div>
    </section>
  );
}

function HistoryRow({ item }: { item: OnboardingSuggestionHistoryItem }) {
  const meta = auditStatusMeta(item.status);
  return (
    <li
      aria-label={`${fieldMeta(item.field).label} 감사 기록`}
      className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-[var(--color-fg)]">
          {fieldMeta(item.field).label}
        </span>
        <Badge tone={meta.tone}>{meta.label}</Badge>
      </div>
      <SuggestionValueDisplay field={item.field} value={item.value} />
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-muted)]">
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>{item.source ? sourceLabel(item.source) : "출처 없음"}</span>
          {typeof item.confidence === "number" ? (
            <span>신뢰도 {formatPercent(item.confidence)}</span>
          ) : null}
        </span>
        <time dateTime={item.created_at} className="tabular-nums">
          {formatDateTime(item.created_at)}
        </time>
      </div>
      {item.reason ? (
        <p className="text-xs text-[var(--color-muted)]">{item.reason}</p>
      ) : null}
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
