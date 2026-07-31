import { useEffect, useState } from "react";
import { Badge } from "@/shared/components/ui";
import { formatRelativeTime } from "@/shared/lib";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";

const NEVER_COMPUTED_LABEL = "첫 계산 대기";
const BASELINE_SUFFIX = " 기준";
const STALE_SUFFIX = " · 갱신 필요";

/**
 * 상대 라벨 재계산 tick(ms). 폴링이 정지해도(정착·failed) "N분 전" 라벨이 스스로
 * 늙게 하는 유일한 재렌더 원천이다. 라벨 분해능이 분 단위라 30s 면 분 경계 안쪽에서
 * 갱신을 보장하기에 충분하고, 정착-idle recheck(60s)보다 촘촘해 배지가 recheck
 * 사이에서도 정직하다(설계 §7, 매직값은 상수로 — §4.5-1).
 */
const BADGE_TICK_MS = 30_000;

export interface SnapshotFreshnessBadgeProps {
  snapshot?: OperatorStrategyCandidatesResponse;
  /** 상대 라벨 재계산 주기(ms) 오버라이드. 테스트 가속용. */
  tickMs?: number;
}

/**
 * "N분 전 기준" 신선도 배지 (설계 §7).
 *
 * `computed_at === null`(계산된 적 없음)은 `stale=false` 로 오지만 "최신"이 아니라
 * **부트스트랩**이라 별도 문구를 쓴다(소비자 주의 3). `stale=true` 는 시간 경과와
 * 전략 편집 둘 다에서 켜지고, 실패 쿨다운 중에는 재계산이 큐에 없을 수도 있어
 * "갱신 중"을 약속하지 못한다 — 그래서 "갱신 필요"까지만 말한다(주의 1).
 *
 * 정착·failed 에서는 폴링이 멈춰 재렌더가 없으므로, `now` 를 tick 으로 늙혀 상대
 * 라벨이 "3분 전 기준"에 얼어붙어 거짓 최신을 주장하지 않게 한다(Finding 1b —
 * SnapshotStatusNotice.useElapsedSeconds 와 같은 tick shape). 24h 초과 시
 * `formatRelativeTime` 이 KST 절대 시각으로 폴백하는 경로는 그대로 재사용한다.
 */
export function SnapshotFreshnessBadge({
  snapshot,
  tickMs = BADGE_TICK_MS
}: SnapshotFreshnessBadgeProps) {
  const hasComputedAt = snapshot?.computed_at != null;
  const now = useNow(hasComputedAt, tickMs);

  if (!snapshot) return null;
  if (snapshot.computed_at == null) {
    return <Badge tone="muted">{NEVER_COMPUTED_LABEL}</Badge>;
  }
  return (
    <Badge tone={snapshot.stale ? "watch" : "muted"}>
      {`${formatRelativeTime(snapshot.computed_at, now)}${BASELINE_SUFFIX}${
        snapshot.stale ? STALE_SUFFIX : ""
      }`}
    </Badge>
  );
}

/**
 * `active` 동안 tickMs 주기로 갱신되는 현재 시각 — 상대 라벨을 스스로 늙힌다.
 * `active=false`(계산된 적 없음)면 타이머를 돌리지 않는다. 소비자가 하나뿐이라 아직
 * 공용화하지 않는다(§4.5-6 — SnapshotStatusNotice.useElapsedSeconds 와 같은 shape;
 * 두 번째 소비자가 생기면 `shared/hooks/` 로 승격).
 */
function useNow(active: boolean, tickMs: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    setNow(new Date());
    const timer = window.setInterval(() => setNow(new Date()), tickMs);
    return () => window.clearInterval(timer);
  }, [active, tickMs]);
  return now;
}
