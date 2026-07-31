import { useEffect, useState } from "react";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";

const RUNNING_LABEL = "미리보기를 다시 계산하고 있습니다…";
const FIRST_RUN_HINT = "최초 계산은 수십 초가 걸릴 수 있습니다.";
const FAILED_TITLE = "최근 갱신이 실패했습니다";
const FAILED_WITH_SNAPSHOT =
  "직전에 성공한 계산 결과를 그대로 표시합니다. 새로고침으로 다시 시도할 수 있습니다.";
const FAILED_WITHOUT_SNAPSHOT = "표시할 이전 결과가 없습니다. 새로고침으로 다시 시도해 주세요.";
const ELAPSED_TICK_MS = 1_000;

export interface SnapshotStatusNoticeProps {
  snapshot?: OperatorStrategyCandidatesResponse;
}

/**
 * 갱신 중 인디케이터(+경과 안내)와 실패 경고 (설계 §7, ExperimentRunProgress 패턴).
 *
 * "갱신 중"은 `snapshot_status === "running"` 에서만 말한다 — `stale` 로는 말하지
 * 않는다(소비자 주의 1). 실패는 접근성 경고로 띄우되 목록을 지우지 않는다: 이전
 * 성공분이 남아 있으면 그대로 유효하다(주의 2). 경과 안내는 스냅샷이 아직 없는
 * 최초 계산(온보딩 첫 진입)에서만 덧붙인다.
 */
export function SnapshotStatusNotice({ snapshot }: SnapshotStatusNoticeProps) {
  const running = snapshot?.snapshot_status === "running";
  const elapsedSeconds = useElapsedSeconds(running);

  if (!snapshot) return null;

  if (running) {
    const bootstrap = snapshot.computed_at == null;
    return (
      <p
        role="status"
        data-testid="snapshot-progress"
        className="flex items-center gap-2 text-xs text-[var(--color-muted)]"
      >
        <span
          aria-hidden="true"
          className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]"
        />
        <span>
          {`${RUNNING_LABEL} ${elapsedSeconds}초 경과`}
          {bootstrap ? ` · ${FIRST_RUN_HINT}` : ""}
        </span>
      </p>
    );
  }

  if (snapshot.snapshot_status === "failed") {
    return (
      <div
        role="alert"
        data-testid="snapshot-failed"
        className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] p-2 text-xs"
      >
        <p className="font-medium text-[color-mix(in_oklch,var(--color-danger),black_30%)]">
          {FAILED_TITLE}
        </p>
        <p className="text-[var(--color-fg)]">
          {snapshot.computed_at == null ? FAILED_WITHOUT_SNAPSHOT : FAILED_WITH_SNAPSHOT}
        </p>
      </div>
    );
  }

  return null;
}

/**
 * `active` 가 켜진 시점부터의 경과 초. 폴링 재조회(running→running)로는 리셋되지
 * 않고 `active` 전이에만 반응한다. 소비자가 하나뿐이라 아직 공용화하지 않는다
 * (§4.5-6 — 두 번째 소비자가 생기면 `shared/hooks/` 로 승격).
 */
function useElapsedSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    if (!active) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1_000));
    }, ELAPSED_TICK_MS);
    return () => window.clearInterval(timer);
  }, [active]);
  return seconds;
}
