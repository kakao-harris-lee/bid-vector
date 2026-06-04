import { AlertTriangle, CheckCircle2, Target } from "lucide-react";
import { Badge } from "@/shared/components/ui";
import { t } from "@/shared/i18n";
import { MiniBar } from "./Helpers";

export type DecisionAction = "bid_now" | "review" | "skip";

const ACTION_LABEL: Record<DecisionAction, string> = {
  bid_now: t("decision_reasons.action_bid_now"),
  review: t("decision_reasons.action_review"),
  skip: t("decision_reasons.action_skip")
};

const ACTION_VERDICT: Record<DecisionAction, string> = {
  bid_now: t("decision_reasons.verdict_bid_now"),
  review: t("decision_reasons.verdict_review"),
  skip: t("decision_reasons.verdict_skip")
};

const ACTION_TONE: Record<DecisionAction, "healthy" | "watch" | "muted"> = {
  bid_now: "healthy",
  review: "watch",
  skip: "muted"
};

export interface DecisionReasonsCardProps {
  strengths?: string[] | null;
  riskFlags?: string[] | null;
  action: DecisionAction;
  priorityScore: number;
  probabilityScore?: number | null;
  /** 컴팩트 모드: 패딩/폰트 축소, 타임라인 임베드용 */
  compact?: boolean;
  className?: string;
}

export function DecisionReasonsCard({
  strengths,
  riskFlags,
  action,
  priorityScore,
  probabilityScore,
  compact = false,
  className
}: DecisionReasonsCardProps) {
  const safeStrengths = strengths ?? [];
  const safeRisks = riskFlags ?? [];

  const sectionGap = compact ? "gap-2" : "gap-3";
  const titleSize = compact ? "text-[11px]" : "text-xs";
  const bodySize = compact ? "text-[11px]" : "text-xs";

  return (
    <div
      className={`flex flex-col ${sectionGap} rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3${
        className ? ` ${className}` : ""
      }`}
      aria-label={t("decision_reasons.title")}
    >
      {/* 왜 가능한가 */}
      <section className="flex flex-col gap-1">
        <h4
          className={`flex items-center gap-1 font-semibold uppercase tracking-wide text-[color-mix(in_oklch,var(--color-success),black_25%)] ${titleSize}`}
        >
          <CheckCircle2 size={13} aria-hidden="true" />
          {t("decision_reasons.strengths_title")}
        </h4>
        {safeStrengths.length ? (
          <ul className={`flex flex-wrap gap-1 ${bodySize}`} aria-label={t("decision_reasons.strengths_title")}>
            {safeStrengths.map((item, index) => (
              <li
                key={`strength-${index}`}
                className="rounded-full bg-[color-mix(in_oklch,var(--color-success),white_78%)] px-2 py-0.5 text-[color-mix(in_oklch,var(--color-success),black_30%)]"
              >
                {item}
              </li>
            ))}
          </ul>
        ) : (
          <p className={`text-[var(--color-muted)] ${bodySize}`}>{t("decision_reasons.strengths_empty")}</p>
        )}
      </section>

      {/* 왜 위험한가 */}
      <section className="flex flex-col gap-1">
        <h4
          className={`flex items-center gap-1 font-semibold uppercase tracking-wide text-[color-mix(in_oklch,var(--color-danger),black_20%)] ${titleSize}`}
        >
          <AlertTriangle size={13} aria-hidden="true" />
          {t("decision_reasons.risks_title")}
        </h4>
        {safeRisks.length ? (
          <ul className={`flex flex-col gap-1 ${bodySize}`} aria-label={t("decision_reasons.risks_title")}>
            {safeRisks.map((item, index) => (
              <li
                key={`risk-${index}`}
                className="rounded-md bg-[color-mix(in_oklch,var(--color-warn),white_80%)] px-2 py-1 text-[color-mix(in_oklch,var(--color-warn),black_45%)]"
              >
                {item}
              </li>
            ))}
          </ul>
        ) : (
          <p className={`text-[var(--color-muted)] ${bodySize}`}>{t("decision_reasons.risks_empty")}</p>
        )}
      </section>

      {/* 들어가도 되는가 */}
      <section className="flex flex-col gap-1.5 border-t border-[var(--color-border)] pt-2">
        <h4 className={`flex items-center gap-1 font-semibold uppercase tracking-wide text-[var(--color-muted)] ${titleSize}`}>
          <Target size={13} aria-hidden="true" />
          {t("decision_reasons.verdict_title")}
        </h4>
        <div className="flex items-center gap-2">
          <Badge tone={ACTION_TONE[action]}>{ACTION_LABEL[action]}</Badge>
          <span className={`text-[var(--color-fg)] ${bodySize}`}>{ACTION_VERDICT[action]}</span>
        </div>
        <div className={`flex flex-col gap-1 ${bodySize}`}>
          <ScoreGauge label={t("decision_reasons.priority_label")} value={priorityScore} tone="teal" />
          {probabilityScore != null ? (
            <ScoreGauge
              label={t("decision_reasons.probability_label")}
              value={probabilityScore}
              tone="teal"
            />
          ) : null}
        </div>
      </section>
    </div>
  );
}

function ScoreGauge({
  label,
  value,
  tone
}: {
  label: string;
  value: number;
  tone: "teal" | "amber";
}) {
  const pct = Math.round(Math.max(0, Math.min(1, value || 0)) * 100);
  return (
    <div className="flex items-center gap-2">
      <span className="w-14 shrink-0 text-[var(--color-muted)]">{label}</span>
      <MiniBar value={value} tone={tone} />
      <span className="w-9 shrink-0 text-right tabular-nums text-[var(--color-fg)]">{pct}%</span>
    </div>
  );
}
