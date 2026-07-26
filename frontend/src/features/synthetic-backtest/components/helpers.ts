import type {
  SyntheticExperimentSampleGapItem,
  SyntheticExperimentSampleGapOperatorTarget
} from "@/shared/types/synthetic";

export const MIXED_DATA_WARNING = "canonical_synthetic_mixed";

export const GAP_DIMENSION_LABEL: Record<string, string> = {
  preset: "preset",
  category: "카테고리",
  business_type: "업종",
  budget_band: "예산구간"
};

export const NEXT_STEP_LABEL: Record<string, string> = {
  resolve_mixed_data: "혼합 데이터 정리",
  run_existing_experiment: "기존 실험 선택",
  save_preset: "preset 저장",
  create_experiment: "실험 생성"
};

export function gapTitle(gap: SyntheticExperimentSampleGapItem): string {
  return `${GAP_DIMENSION_LABEL[gap.dimension] ?? gap.dimension} · ${gap.key}`;
}

export function hasMixedWarning(warnings?: string[] | null): boolean {
  return (warnings ?? []).includes(MIXED_DATA_WARNING);
}

export function boolValue(value?: boolean | null): string {
  if (value === true) return "true";
  if (value === false) return "false";
  return "unknown";
}

export function unresolvedOperatorTargets(
  targets?: SyntheticExperimentSampleGapOperatorTarget[] | null
): SyntheticExperimentSampleGapOperatorTarget[] {
  return (targets ?? []).filter(
    (target) =>
      target.resolved === false ||
      target.operator_id_scope_ready === false ||
      target.operator_id == null
  );
}
