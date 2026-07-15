import type { BadgeTone } from "@/shared/components/ui";

/**
 * 실행 상태(`queued/running/completed/failed`) → Badge tone 룩업.
 *
 * synthetic-backtest 화면 두 곳(`ExperimentRunProgress`, `ExperimentList`)에
 * 동일한 매핑이 복붙돼 있던 것을 한 곳으로 모은 단일 출처입니다. 목록에 없는
 * 상태(`queued` 등)와 미정 상태는 `muted`로 정직하게 표기합니다.
 *
 * 주의: `strategy/RecentRuns`와 `operations/OperationsScreen`은 `running/queued`를
 * `watch`로 보는 **다른 의미**(진행 중 강조)를 쓰고 기본값도 달라, 의도적으로
 * 이 맵을 공유하지 않습니다.
 */
export const EXPERIMENT_RUN_TONE = {
  completed: "healthy",
  failed: "critical",
  running: "info"
} as const satisfies Record<string, BadgeTone>;

/** 임의 문자열/미정도 받아, 목록에 없는 상태는 `muted`로 돌려주는 안전한 룩업. */
export function experimentRunTone(status: string | null | undefined): BadgeTone {
  if (!status) return "muted";
  return EXPERIMENT_RUN_TONE[status as keyof typeof EXPERIMENT_RUN_TONE] ?? "muted";
}
