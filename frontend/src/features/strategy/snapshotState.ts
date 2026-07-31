import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";

/**
 * preview 스냅샷 상태 해석 — 폴링·렌더 게이트의 순수 코어 (설계 2026-07-30 §7).
 *
 * PR-B 이후 `GET /operator/strategy/candidates` 는 요청 경로에서 ML 스캔을 하지
 * 않고 스냅샷 행을 순수 읽기한다. 그래서 프론트의 판단 근거는 오직 응답 메타
 * (`snapshot_status`/`computed_at`/`stale`)이고, 그 해석에 소비자 주의 4건이
 * 전부 걸려 있어 컴포넌트 안 조건식이 아니라 이 모듈에 모아 테스트한다(§4.7-4).
 */

/**
 * 폴링 주기(ms). 스냅샷 재계산은 분석 예산(250건)에 묶여 수십 초 규모라
 * ExperimentRunProgress(1.5s)보다 느슨하게 잡는다 — 매직값을 컴포넌트에 두지
 * 않는다(§4.5-1).
 */
export const SNAPSHOT_POLL_INTERVAL_MS = 3_000;

/**
 * 더 물어봐도 상태가 바뀌지 않는가(= 폴링 정지 조건).
 *
 * - `running`: 미정착 — 재계산이 진행 중이다.
 * - `failed`: **정착**. 이전 성공 뒤 실패한 행은 `failed` + `stale=false` 로 오고
 *   실패 쿨다운(60s) 동안 자동 재디스패치도 안 되므로 계속 물어도 같은 답이다.
 *   복구는 사용자의 명시 갱신(POST /candidates/refresh)이 담당한다.
 * - `idle` + `computed_at === null`: **미정착**. 계산된 적 없는 행은 `stale=false`
 *   로 오지만 "최신"이 아니라 "부트스트랩"이다 — 다음 GET 이 자동 디스패치하므로
 *   한두 틱 뒤 `running` 또는 `failed` 로 정착한다(livelock 없음).
 */
export function isSnapshotSettled(
  data: OperatorStrategyCandidatesResponse | undefined
): boolean {
  if (!data) return false;
  if (data.snapshot_status === "running") return false;
  if (data.snapshot_status === "failed") return true;
  return data.computed_at != null;
}

/**
 * react-query `refetchInterval` 콜백의 순수 코어.
 *
 * 정착이면 멈춘다. 마지막 fetch 가 실패했으면(`errored`) 멈춘다 — 백엔드가 죽었을
 * 때 열린 탭이 영구 재시도하지 않게 한다. 전역 `retry: 1` 이 일시 장애를 이미 한
 * 번 흡수하고, 새로고침의 invalidate 가 성공하면 폴링은 자동 재개된다.
 */
export function snapshotPollInterval(
  data: OperatorStrategyCandidatesResponse | undefined,
  errored: boolean,
  intervalMs: number = SNAPSHOT_POLL_INTERVAL_MS
): number | false {
  if (errored) return false;
  return isSnapshotSettled(data) ? false : intervalMs;
}

/**
 * 한 번이라도 성공 계산된 스냅샷이 있는가 — 통계·후보 목록의 렌더 게이트.
 *
 * 부트스트랩 응답은 `evaluated_project_count: 0` / `candidates: []` 인데 그대로
 * 그리면 "평가 0건 / 매칭되는 후보가 없습니다"라는 거짓이 된다(§2 정직 명세).
 * `snapshot_status === "failed"` 여도 `computed_at` 이 있으면 직전 성공분은
 * 유효하므로 계속 보여준다(소비자 주의 2).
 */
export function hasComputedSnapshot(
  data: OperatorStrategyCandidatesResponse | undefined
): boolean {
  return data?.computed_at != null;
}
