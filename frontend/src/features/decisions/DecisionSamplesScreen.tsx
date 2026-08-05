import { useCallback, useState } from "react";
import { Download } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { useDecisionSamplesQuery } from "./hooks";
import { fetchDecisionSamplesCsv } from "@/shared/api";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  toastApi
} from "@/shared/components/ui";
import { formatCurrency, formatDateTime, formatPercent } from "@/shared/lib";
import {
  AMOUNT_BASIS_TEXT_LABEL,
  BID_RATE_ON_ESTIMATE_LABEL
} from "@/shared/constants/amountBasis";
import type { DecisionSampleItem } from "@/shared/types/decisionSamples";

const SAMPLE_DAYS_OPTIONS = [7, 30, 90] as const;
type SampleDays = (typeof SAMPLE_DAYS_OPTIONS)[number];

const SAMPLE_LIMIT = 50;
const DASH = "—";

/** 0~1 비율을 % 문자열로. null/undefined면 데이터 부재 대시. */
function rateOrDash(value?: number | null): string {
  if (value === null || value === undefined) return DASH;
  return formatPercent(value);
}

/** action/decision_status 라벨에 어울리는 Badge tone. 미지정 상태는 info. */
const DECISION_STATUS_TONE: Record<string, "info" | "healthy" | "watch" | "critical"> = {
  submitted: "healthy",
  skipped: "critical",
  reviewing: "watch"
};

function statusTone(status?: string | null): "info" | "healthy" | "watch" | "critical" {
  return DECISION_STATUS_TONE[status ?? ""] ?? "info";
}

export function DecisionSamplesScreen() {
  const { session } = useShellContext();
  const token = session?.token ?? null;
  const [days, setDays] = useState<SampleDays>(30);
  const [busy, setBusy] = useState(false);

  const query = useDecisionSamplesQuery(session, { days, limit: SAMPLE_LIMIT });
  const data = query.data;
  const samples = data?.samples ?? [];
  const hasSamples = (data?.sample_count ?? 0) > 0;

  const handleDownloadCsv = useCallback(async () => {
    setBusy(true);
    try {
      const csv = await fetchDecisionSamplesCsv({ days, limit: SAMPLE_LIMIT }, token);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "decision-samples.csv";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch {
      toastApi.danger({ title: "증적 CSV 다운로드에 실패했습니다." });
    } finally {
      setBusy(false);
    }
  }, [days, token]);

  return (
    <section className="flex flex-col gap-4" aria-label="의사결정 증적 샘플">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold text-[var(--color-fg)]">의사결정 증적 샘플</h2>
          <p className="text-xs text-[var(--color-muted)]">
            시스템이 산출·기록한 최근 추천/예측의 감사 증적입니다 — 정산·실낙찰 결과가 아닙니다.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs">
            기간
            <select
              value={days}
              onChange={(event) => setDays(Number(event.target.value) as SampleDays)}
              aria-label="증적 기간 (일)"
              className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
            >
              {SAMPLE_DAYS_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value}일
                </option>
              ))}
            </select>
          </label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleDownloadCsv}
            disabled={busy || !hasSamples}
          >
            <Download className="h-4 w-4" />
            CSV 다운로드
          </Button>
        </div>
      </header>

      {query.isPending && !data ? (
        <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
      ) : null}

      {query.error ? (
        <p
          role="alert"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_82%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          {query.error.message ?? "의사결정 증적 샘플을 불러오지 못했습니다."}
        </p>
      ) : null}

      {data ? (
        hasSamples ? (
          <SamplesCard samples={samples} truncated={data.truncated} limit={data.limit} />
        ) : (
          <Card>
            <CardContent className="py-6">
              <p className="text-sm text-[var(--color-muted)]">
                기록된 추천/예측 증적이 아직 없습니다.
              </p>
            </CardContent>
          </Card>
        )
      ) : null}

      {data?.notes ? <CaveatCard notes={data.notes} /> : null}
    </section>
  );
}

function SamplesCard({
  samples,
  truncated,
  limit
}: {
  samples: DecisionSampleItem[];
  truncated: boolean;
  limit: number;
}) {
  return (
    <Card aria-label="증적 목록">
      <CardHeader className="flex-row items-baseline justify-between">
        <CardTitle>증적 목록</CardTitle>
        <span className="text-[11px] text-[var(--color-muted)]">
          {samples.length.toLocaleString("ko-KR")}건
        </span>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {truncated ? (
          <p className="text-[11px] text-[var(--color-warn)]">
            상한({limit.toLocaleString("ko-KR")}건)에 도달해 더 오래된 기록은 잘렸습니다.
          </p>
        ) : null}
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                <th className="py-1.5 pr-2 font-medium">공고명</th>
                <th className="py-1.5 pr-2 font-medium">분야</th>
                <th className="py-1.5 pr-2 text-right font-medium">
                  {AMOUNT_BASIS_TEXT_LABEL.submission}
                </th>
                {/* 이 응답의 율은 추정가격 분모다(app/services/decision_samples.py) —
                    적격심사가 보는 기초금액 기준 율과 같은 이름을 쓰지 않는다. */}
                <th className="py-1.5 pr-2 text-right font-medium">
                  {BID_RATE_ON_ESTIMATE_LABEL}
                </th>
                <th className="py-1.5 pr-2 text-right font-medium">가격 적합도(추정)</th>
                <th className="py-1.5 pr-2 font-medium">상태</th>
                <th className="py-1.5 pr-2 text-right font-medium">예측가</th>
                <th className="py-1.5 pr-2 font-medium">예측기</th>
                <th className="py-1.5 font-medium">기록 시각</th>
              </tr>
            </thead>
            <tbody>
              {samples.map((sample) => (
                <tr
                  key={sample.decision_record_id}
                  className="border-b border-[var(--color-border)] last:border-0"
                >
                  <td
                    className="max-w-[16rem] truncate py-1.5 pr-2 text-[var(--color-fg)]"
                    title={
                      sample.reasoning
                        ? `${sample.title}\n\n근거: ${sample.reasoning}`
                        : sample.title
                    }
                  >
                    {sample.title || "(제목 없음)"}
                    {sample.notice_number ? (
                      <span className="block text-[10px] text-[var(--color-muted)]">
                        {sample.notice_number}
                      </span>
                    ) : null}
                  </td>
                  <td className="py-1.5 pr-2 text-[var(--color-muted)]">
                    {sample.category ?? "(미분류)"}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">
                    {formatCurrency(sample.recommended_amount)}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">
                    {rateOrDash(sample.recommended_bid_rate)}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">
                    {rateOrDash(sample.probability_score)}
                  </td>
                  <td className="py-1.5 pr-2">
                    <Badge tone={statusTone(sample.decision_status)}>
                      {sample.decision_status ?? sample.action ?? DASH}
                    </Badge>
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">
                    {sample.prediction
                      ? formatCurrency(sample.prediction.predicted_price)
                      : DASH}
                  </td>
                  <td className="py-1.5 pr-2 text-[var(--color-muted)]">
                    {sample.prediction?.predictor_name ?? DASH}
                  </td>
                  <td className="py-1.5 text-[var(--color-muted)]">
                    {sample.decided_at ? formatDateTime(sample.decided_at) : DASH}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function CaveatCard({ notes }: { notes: string }) {
  return (
    <Card aria-label="해석 주의사항">
      <CardHeader>
        <CardTitle className="text-sm">해석 주의사항</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-xs leading-relaxed text-[var(--color-muted)]">{notes}</p>
      </CardContent>
    </Card>
  );
}
