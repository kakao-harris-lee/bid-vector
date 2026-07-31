import { Badge } from "@/shared/components/ui";
import { formatRelativeTime } from "@/shared/lib";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";

const NEVER_COMPUTED_LABEL = "첫 계산 대기";
const BASELINE_SUFFIX = " 기준";
const STALE_SUFFIX = " · 갱신 필요";

export interface SnapshotFreshnessBadgeProps {
  snapshot?: OperatorStrategyCandidatesResponse;
}

/**
 * "N분 전 기준" 신선도 배지 (설계 §7).
 *
 * `computed_at === null`(계산된 적 없음)은 `stale=false` 로 오지만 "최신"이 아니라
 * **부트스트랩**이라 별도 문구를 쓴다(소비자 주의 3). `stale=true` 는 시간 경과와
 * 전략 편집 둘 다에서 켜지고, 실패 쿨다운 중에는 재계산이 큐에 없을 수도 있어
 * "갱신 중"을 약속하지 못한다 — 그래서 "갱신 필요"까지만 말한다(주의 1).
 */
export function SnapshotFreshnessBadge({ snapshot }: SnapshotFreshnessBadgeProps) {
  if (!snapshot) return null;
  if (snapshot.computed_at == null) {
    return <Badge tone="muted">{NEVER_COMPUTED_LABEL}</Badge>;
  }
  return (
    <Badge tone={snapshot.stale ? "watch" : "muted"}>
      {`${formatRelativeTime(snapshot.computed_at)}${BASELINE_SUFFIX}${
        snapshot.stale ? STALE_SUFFIX : ""
      }`}
    </Badge>
  );
}
