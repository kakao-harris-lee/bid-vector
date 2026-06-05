import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { formatCurrency, formatDateTime, formatPercent } from "@/shared/lib";
import { t } from "@/shared/i18n";
import { IconButton } from "@/app/layout/IconButton";
import { trackProjectView } from "@/shared/api/operations";
import type { BidDecisionActionType } from "@/shared/api";
import type { AuthSession } from "@/app/layout/AuthGate";
import type { DetailSelection } from "../types";
import { useApplyBidDecisionActionMutation } from "../hooks";
import { DecisionReasonsCard } from "./DecisionReasonsCard";
import { InlineActionButtons } from "./InlineActionButtons";

export function DetailDrawer({
  selection,
  onClose,
  username,
  authToken,
  session = null,
  activeOperatorId = null
}: {
  selection: DetailSelection | null;
  onClose: () => void;
  username: string | null;
  authToken?: string | null;
  /**
   * When supplied, the drawer renders the same inline submit/review/skip
   * action buttons as `OpportunityRow` for decision-source items. Tests can
   * omit these to keep the legacy display-only behaviour.
   */
  session?: AuthSession | null;
  activeOperatorId?: number | null;
}) {
  const mutation = useApplyBidDecisionActionMutation(session ?? null);
  const [pendingAction, setPendingAction] = useState<BidDecisionActionType | null>(
    null
  );
  // Roadmap C-1 (a): when a tender/opportunity opens in the drawer, log a
  // `project_view` analytics event. The backend coalesces multiple views into
  // a single first-view per (operator, project) so we don't have to guard
  // against repeats here.
  useEffect(() => {
    if (!selection) return;
    const id = selection.item.project?.project_id;
    if (!id || !authToken) return;
    void trackProjectView(id, authToken);
  }, [selection, authToken]);

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
          decisionRecordId={selection.item.decision_record_id ?? null}
          projectId={selection.item.project?.project_id ?? null}
          authToken={authToken ?? null}
        />
      ) : null}
      {selection.kind === "opportunity" &&
      selection.item.source === "decision" &&
      typeof selection.item.decision_record_id === "number" &&
      session?.token ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[var(--color-border)] pt-3">
          <InlineActionButtons
            decisionStatus={selection.item.decision_status}
            pendingAction={pendingAction}
            onAction={(action) => {
              if (!selection.item.decision_record_id) return;
              setPendingAction(action);
              mutation.mutate(
                {
                  decisionRecordId: selection.item.decision_record_id,
                  action,
                  operatorId: activeOperatorId
                },
                { onSettled: () => setPendingAction(null) }
              );
            }}
          />
        </div>
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
