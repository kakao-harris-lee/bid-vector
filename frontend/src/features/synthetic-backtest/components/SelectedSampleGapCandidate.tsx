import { Badge, Button } from "@/shared/components/ui";
import type { SyntheticExperimentSampleGapRunCandidateResponse } from "@/shared/types/synthetic";
import {
  NEXT_STEP_LABEL,
  boolValue,
  gapTitle,
  hasMixedWarning,
  unresolvedOperatorTargets
} from "./helpers";

export function SelectedSampleGapCandidate({
  candidate,
  selecting,
  saving,
  onSelectExperiment,
  onSavePreset
}: {
  candidate: SyntheticExperimentSampleGapRunCandidateResponse;
  selecting: boolean;
  saving: boolean;
  onSelectExperiment: (experimentId: number) => void;
  onSavePreset: (name: string) => void;
}) {
  const blockedWarnings = candidate.blocked_by_warnings ?? [];
  const hasMixedBlock = hasMixedWarning(blockedWarnings) || hasMixedWarning(candidate.warnings);
  const unresolvedTargets = unresolvedOperatorTargets(candidate.operator_targets);
  const operatorScopeBlocked =
    candidate.operator_id_scope_ready === false || unresolvedTargets.length > 0;
  const actionBlocked = !candidate.run_allowed || hasMixedBlock || operatorScopeBlocked;

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--color-primary)] px-3 py-2"
      aria-label="선택된 sample-gap 후보"
    >
      {hasMixedBlock ? (
        <div className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_92%)] px-2 py-1 text-[var(--color-danger)]">
          {candidate.message}
        </div>
      ) : null}
      {operatorScopeBlocked ? (
        <div className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_92%)] px-2 py-1 text-[var(--color-danger)]">
          operator_id scope가 준비되지 않았습니다. 이 실행 경로를 선택하거나 저장하기 전에 synthetic operator target을 먼저 정리하세요.
        </div>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-[var(--color-fg)]">
          {candidate.preset_name ?? gapTitle(candidate.gap)}
        </span>
        <Badge tone={actionBlocked ? "critical" : "info"}>
          {NEXT_STEP_LABEL[candidate.next_step] ?? candidate.next_step}
        </Badge>
      </div>
      <dl className="grid grid-cols-2 gap-1 text-[var(--color-muted)]">
        <dt>추천 action</dt>
        <dd className="text-right text-[var(--color-fg)]">{candidate.action_label}</dd>
        <dt>카테고리</dt>
        <dd className="text-right text-[var(--color-fg)]">
          {candidate.params.category ?? "전체"}
        </dd>
        <dt>limit</dt>
        <dd className="text-right text-[var(--color-fg)]">{candidate.params.limit}</dd>
        <dt>참여 회사</dt>
        <dd className="text-right text-[var(--color-fg)]">
          {candidate.operator_slugs && candidate.operator_slugs.length > 0
            ? `${candidate.operator_slugs.length}개`
            : "전체"}
        </dd>
        <dt>operator_id_scope_ready</dt>
        <dd className="text-right text-[var(--color-fg)]">
          {boolValue(candidate.operator_id_scope_ready)}
        </dd>
        <dt>run_allowed</dt>
        <dd className="text-right text-[var(--color-fg)]">{boolValue(candidate.run_allowed)}</dd>
        <dt>blocked_by_warnings</dt>
        <dd className="text-right text-[var(--color-fg)]">
          {blockedWarnings.length > 0 ? blockedWarnings.join(", ") : "none"}
        </dd>
      </dl>
      {unresolvedTargets.length > 0 ? (
        <section
          aria-label="unresolved operator targets"
          className="rounded-md border border-[var(--color-border)] px-2 py-1"
        >
          <h4 className="mb-1 text-[var(--color-muted)]">미해결 operator targets</h4>
          <ul className="flex flex-col gap-1">
            {unresolvedTargets.map((target) => (
              <li
                key={target.slug}
                className="flex flex-wrap items-center justify-between gap-2 text-[var(--color-fg)]"
              >
                <span>{target.slug}</span>
                <span className="text-[var(--color-muted)]">
                  {target.username ?? "username unknown"} · operator_id{" "}
                  {target.operator_id ?? "unresolved"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {candidate.preset_name && !candidate.experiment_id ? (
          <Button
            type="button"
            size="sm"
            onClick={() => onSavePreset(candidate.preset_name as string)}
            disabled={saving || actionBlocked}
          >
            {saving ? "저장 중…" : "Preset 저장"}
          </Button>
        ) : null}
        {candidate.experiment_id ? (
          <Button
            type="button"
            size="sm"
            onClick={() => onSelectExperiment(candidate.experiment_id as number)}
            disabled={selecting || actionBlocked}
          >
            {selecting ? "선택 중…" : "기존 실험 선택"}
          </Button>
        ) : null}
        {actionBlocked ? (
          <Button type="button" size="sm" variant="outline" disabled>
            경고 확인 필요
          </Button>
        ) : null}
      </div>
    </div>
  );
}
