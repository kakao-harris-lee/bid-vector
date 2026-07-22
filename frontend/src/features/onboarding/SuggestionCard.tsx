import { useState } from "react";
import { Check, CheckCircle2, Pencil, Search, X } from "lucide-react";
import { Badge, Button } from "@/shared/components/ui";
import { cn, formatPercent } from "@/shared/lib";
import type { OnboardingFieldSuggestion, OnboardingSuggestionValue } from "@/shared/api";
import {
  DECISION_STATUS_META,
  fieldMeta,
  sourceLabel,
  type DecisionStatus
} from "./constants";
import type { DecisionState } from "./decisions";
import { SuggestionValueDisplay, SuggestionValueEditor } from "./SuggestionValueEditor";

/**
 * 상태별 컨테이너 스타일 — 색만이 아니라 테두리 모양(점선/실선)으로도 구분한다(접근성).
 * pending 은 점선 + 틴트로 "확정 아님(draft)"을 시각적으로 분리한다(§2 정직 명세).
 */
const CARD_TONE_CLASS: Record<DecisionStatus, string> = {
  pending:
    "border-dashed border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_92%)]",
  accepted: "border-solid border-[var(--color-success)] bg-[var(--color-card)]",
  rejected: "border-solid border-[var(--color-border)] bg-[var(--color-card)] opacity-70"
};

const STATUS_ICON: Record<DecisionStatus, typeof Search> = {
  pending: Search,
  accepted: CheckCircle2,
  rejected: X
};

export interface SuggestionCardProps {
  suggestion: OnboardingFieldSuggestion;
  decision: DecisionState;
  onDecision: (field: string, next: DecisionState) => void;
}

export function SuggestionCard({ suggestion, decision, onDecision }: SuggestionCardProps) {
  const meta = fieldMeta(suggestion.field);
  const statusMeta = DECISION_STATUS_META[decision.status];
  const StatusIcon = STATUS_ICON[decision.status];
  const [editing, setEditing] = useState(false);
  const [draftValue, setDraftValue] = useState<OnboardingSuggestionValue>(decision.value);

  const startEdit = () => {
    setDraftValue(decision.value);
    setEditing(true);
  };
  const cancelEdit = () => setEditing(false);
  const applyEdit = () => {
    onDecision(suggestion.field, { status: "accepted", value: draftValue });
    setEditing(false);
  };
  const accept = () =>
    onDecision(suggestion.field, { status: "accepted", value: decision.value });
  const reject = () =>
    onDecision(suggestion.field, { status: "rejected", value: decision.value });

  return (
    <li
      className={cn("flex flex-col gap-2 rounded-md border p-3", CARD_TONE_CLASS[decision.status])}
      aria-label={`${meta.label} 후보`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-[var(--color-fg)]">{meta.label}</span>
        <Badge tone={statusMeta.tone} className="shrink-0">
          <StatusIcon size={11} aria-hidden="true" />
          {statusMeta.label}
        </Badge>
      </div>

      {editing ? (
        <SuggestionValueEditor
          field={suggestion.field}
          value={draftValue}
          onChange={setDraftValue}
        />
      ) : (
        <SuggestionValueDisplay field={suggestion.field} value={decision.value} />
      )}

      {/* provenance — source/confidence/reason/matched_notice_count 를 숨기지 않는다(§2). */}
      <dl className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-[var(--color-muted)]">
        <div className="flex items-center gap-1">
          <dt className="sr-only">출처</dt>
          <dd>{sourceLabel(suggestion.source)}</dd>
        </div>
        <div className="flex items-center gap-1">
          <dt>신뢰도</dt>
          <dd className="tabular-nums">{formatPercent(suggestion.confidence)}</dd>
        </div>
        <div className="flex items-center gap-1">
          <dt>근거 공고</dt>
          <dd className="tabular-nums">{suggestion.matched_notice_count}건</dd>
        </div>
      </dl>
      <p className="text-[11px] text-[var(--color-muted)]">{suggestion.reason}</p>

      {editing ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="primary" onClick={applyEdit}>
            <Check size={13} aria-hidden="true" /> 수정 값 적용
          </Button>
          <Button size="sm" variant="ghost" onClick={cancelEdit}>
            취소
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={decision.status === "accepted" ? "primary" : "outline"}
            aria-pressed={decision.status === "accepted"}
            onClick={accept}
          >
            수락
          </Button>
          <Button size="sm" variant="outline" onClick={startEdit}>
            <Pencil size={13} aria-hidden="true" /> 수정
          </Button>
          <Button
            size="sm"
            variant={decision.status === "rejected" ? "secondary" : "ghost"}
            aria-pressed={decision.status === "rejected"}
            onClick={reject}
          >
            거부
          </Button>
        </div>
      )}
    </li>
  );
}
