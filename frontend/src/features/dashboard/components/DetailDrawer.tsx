import { X } from "lucide-react";
import { formatCurrency, formatDateTime, formatPercent } from "@/shared/lib";
import { t } from "@/shared/i18n";
import { IconButton } from "@/app/layout/IconButton";
import type { DetailSelection } from "../types";
import { DecisionReasonsCard } from "./DecisionReasonsCard";

export function DetailDrawer({
  selection,
  onClose,
  username
}: {
  selection: DetailSelection | null;
  onClose: () => void;
  username: string | null;
}) {
  if (!selection) return null;

  const project = selection.item.project;
  return (
    <aside className="detail-drawer" aria-label="상세">
      <div className="drawer-head">
        <div>
          <span>{username ?? "operator"}</span>
          <h2>{project.title}</h2>
        </div>
        <IconButton label="닫기" onClick={onClose}>
          <X size={18} />
        </IconButton>
      </div>
      <dl className="detail-grid">
        <div>
          <dt>기관</dt>
          <dd>{project.issuing_agency ?? project.demand_agency ?? "-"}</dd>
        </div>
        <div>
          <dt>예산</dt>
          <dd>{formatCurrency(project.budget_estimate)}</dd>
        </div>
        <div>
          <dt>마감</dt>
          <dd>{project.deadline ? formatDateTime(project.deadline) : "-"}</dd>
        </div>
        {"recommended_amount" in selection.item && selection.item.recommended_amount ? (
          <div>
            <dt>추천가</dt>
            <dd>{formatCurrency(selection.item.recommended_amount)}</dd>
          </div>
        ) : null}
        {"source_label" in selection.item ? (
          <div>
            <dt>출처</dt>
            <dd>{selection.item.source_label}</dd>
          </div>
        ) : null}
        {"bid_amount" in selection.item ? (
          <div>
            <dt>투찰가</dt>
            <dd>{formatCurrency(selection.item.bid_amount)}</dd>
          </div>
        ) : null}
        {"winning_amount" in selection.item ? (
          <>
            <div>
              <dt>낙찰가</dt>
              <dd>{formatCurrency(selection.item.winning_amount)}</dd>
            </div>
            <div>
              <dt>추천 오차</dt>
              <dd>{formatPercent(selection.item.recommendation_error_rate)}</dd>
            </div>
          </>
        ) : null}
      </dl>
      {selection.kind === "opportunity" ? (
        <DecisionReasonsCard
          strengths={selection.item.strengths}
          riskFlags={selection.item.risk_flags}
          action={selection.item.action}
          priorityScore={selection.item.priority_score}
          probabilityScore={selection.item.probability_score}
        />
      ) : null}
      {"reasoning" in selection.item && selection.item.reasoning ? (
        <details className="drawer-reasoning">
          <summary className="cursor-pointer text-xs font-medium text-[var(--color-muted)]">
            {t("decision_reasons.reasoning_toggle")}
          </summary>
          <p className="drawer-note">{selection.item.reasoning}</p>
        </details>
      ) : null}
    </aside>
  );
}
