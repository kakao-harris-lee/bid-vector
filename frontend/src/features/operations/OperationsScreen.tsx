import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { fetchG2EvidenceSummary, fetchOperationsDashboard, queryKeys } from "@/shared/api";
import { Button } from "@/shared/components/ui";
import { OperationsKpiPanel, useOperationsKpiQuery } from "@/features/decisions";
import type {
  G2EvidenceSummaryResponse,
  OperationsDashboardResponse
} from "@/shared/types/operations";
import {
  AdminFocusStrip,
  CrawlHealth,
  G2EvidenceCard,
  MlReleaseCard,
  SmokeTestCard,
  SummaryCard,
  SyntheticValidationCard,
  TelegramHealth
} from "./components";

const REFRESH_INTERVAL_MS = 30_000;
const G2_EVIDENCE_WINDOW_DAYS = 7;
const KPI_DAYS_OPTIONS = [7, 30, 90] as const;
type KpiDays = (typeof KPI_DAYS_OPTIONS)[number];

export function OperationsScreen() {
  const { session, activeOperator } = useShellContext();
  const activeOperatorId = activeOperator.activeOperatorId;
  const [kpiDays, setKpiDays] = useState<KpiDays>(30);
  const g2Evidence = useQuery<G2EvidenceSummaryResponse | null, Error>({
    queryKey: queryKeys.operations.g2Evidence(activeOperatorId, G2_EVIDENCE_WINDOW_DAYS),
    queryFn: () =>
      fetchG2EvidenceSummary(
        { days: G2_EVIDENCE_WINDOW_DAYS },
        session?.token,
        activeOperatorId
      ),
    enabled: Boolean(session?.token),
    staleTime: 30_000
  });
  const operations = useQuery<OperationsDashboardResponse, Error>({
    queryKey: queryKeys.operations.dashboard(activeOperatorId),
    queryFn: () => fetchOperationsDashboard({ days: 7 }, session?.token, activeOperatorId),
    enabled: Boolean(session?.token),
    refetchInterval: (query) => (document.visibilityState === "visible" ? REFRESH_INTERVAL_MS : false),
    refetchIntervalInBackground: false
  });
  // 회사별 KPI는 시스템 헬스(7일 고정)와 별도 윈도우를 가진다 — 운영자가 KPI
  // 패널에서 직접 7/30/90일을 선택할 수 있도록 별도 셀렉터 + 별도 useQuery 호출.
  const operationsKpi = useOperationsKpiQuery(
    session,
    { days: kpiDays, missedLimit: 10 },
    activeOperatorId
  );

  const criticalCards =
    operations.data?.cards.filter((card) => card.status === "critical") ?? [];
  const selectedOperatorLabel =
    activeOperator.currentOperator?.company ||
    activeOperator.currentOperator?.full_name ||
    activeOperator.currentOperator?.username ||
    operations.data?.current_operator_username ||
    "토큰 소유자";

  return (
    <section className="flex flex-col gap-4" aria-label="운영 대시보드">
      <header className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="break-words text-lg font-semibold text-[var(--color-fg)]">
            G-2 운영 증적 대시보드
          </h2>
          <p className="mt-1 max-w-2xl break-words text-xs text-[var(--color-muted)]">
            관리자 전용 화면입니다. G-2 evidence, blocking gaps, operator별 상태 확인에 집중합니다.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs sm:justify-end">
          <span className="text-[var(--color-muted)]">
            30초마다 자동 갱신 (탭 비활성 시 정지)
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => operations.refetch()}
            disabled={operations.isFetching}
            aria-label="새로고침"
          >
            <RefreshCw size={14} />
          </Button>
        </div>
      </header>

      {criticalCards.length > 0 ? (
        <div
          role="alert"
          aria-label="인시던트 알림"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          <strong>인시던트 {criticalCards.length}건:</strong>{" "}
          {criticalCards.map((card) => card.label).join(", ")}
        </div>
      ) : null}

      {operations.error ? (
        <p
          role="alert"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          {operations.error.message ?? "운영 대시보드를 불러오지 못했습니다."}
        </p>
      ) : null}

      {operations.isPending && !operations.data ? (
        <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
      ) : null}

      {operations.data ? (
        <>
          <AdminFocusStrip
            operatorLabel={selectedOperatorLabel}
            operations={operations.data}
            g2Evidence={g2Evidence.data ?? null}
          />

          <G2EvidenceCard
            data={g2Evidence.data ?? null}
            loading={g2Evidence.isPending}
            error={g2Evidence.error}
          />

          <section
            className="flex flex-col gap-2"
            aria-label="G-2 운영 검증 증거"
          >
            <h3 className="text-sm font-semibold text-[var(--color-fg)]">
              G-2 운영 검증 증거
            </h3>
            <SmokeTestCard summary={operations.data.smoke_test} />
            <SyntheticValidationCard summary={operations.data.synthetic_validation} />
          </section>

          <section
            className="flex flex-col gap-2"
            aria-label="보조 운영 상태"
          >
            <h3 className="text-sm font-semibold text-[var(--color-fg)]">
              보조 운영 상태
            </h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {operations.data.cards.map((card) => (
                <SummaryCard key={card.key} card={card} />
              ))}
            </div>
          </section>

          <section
            className="flex flex-col gap-2"
            aria-label="회사별 KPI"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-[var(--color-muted)]">
                현재 선택한 회사 기준 — 시스템 헬스(7일)와 별도 윈도우입니다.
              </p>
              <label className="flex items-center gap-2 text-xs">
                KPI 기간
                <select
                  value={kpiDays}
                  onChange={(event) =>
                    setKpiDays(Number(event.target.value) as KpiDays)
                  }
                  aria-label="KPI 기간 (일)"
                  className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
                >
                  {KPI_DAYS_OPTIONS.map((value) => (
                    <option key={value} value={value}>
                      {value}일
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <OperationsKpiPanel
              data={operationsKpi.data}
              loading={operationsKpi.isPending}
              error={operationsKpi.error}
            />
          </section>

          <section className="flex flex-col gap-2" aria-label="데이터 수집/알림 상태">
            <h3 className="text-sm font-semibold text-[var(--color-fg)]">
              데이터 수집/알림 상태
            </h3>
            <CrawlHealth summary={operations.data.crawl} />
            <TelegramHealth summary={operations.data.notifications} />
            <MlReleaseCard summary={operations.data.ml_release} />
          </section>
        </>
      ) : null}
    </section>
  );
}
