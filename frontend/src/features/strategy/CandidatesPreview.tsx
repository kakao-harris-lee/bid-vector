import { useState } from "react";
import { Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { useShellContext } from "@/app/dashboardContext";
import {
  CandidateList,
  SnapshotFreshnessBadge,
  SnapshotStatusNotice
} from "./components";
import { hasComputedSnapshot } from "./snapshotState";
import { useRefreshStrategyCandidatesMutation, useStrategyCandidatesQuery } from "./hooks";

const CANDIDATE_LIMIT = 5;
const ANALYZED_LABEL = "분석 대상";
const MATCHED_LABEL = "매칭 후보";
/** evaluated_project_count 의 정직한 각주 — 요청 limit 의 산물이 아니다. */
const ANALYZED_HINT = "스냅샷 계산 시 고정 분석 예산 기준 — 요청 개수와 무관합니다.";

export interface CandidatesPreviewProps {
  /** 폴링 주기(ms) 오버라이드. 테스트 가속용(ExperimentRunProgress 패턴). */
  pollIntervalMs?: number;
}

/**
 * 전략 preview 스냅샷 카드 (설계 2026-07-30 §7).
 *
 * 백엔드는 `GET /operator/strategy/candidates` 를 **순수 읽기**로 서빙한다(PR-B):
 * 마지막 계산 결과 + `computed_at`/`snapshot_status`/`stale` 메타. 그래서 이 카드는
 * "불러오는 중"이 아니라 **저장된 스냅샷을 즉시** 그리고, 서버가 `running` 인 동안만
 * 폴링한다(정착 판정은 `snapshotState.isSnapshotSettled`).
 *
 * 백엔드 리뷰가 PR-C 로 넘긴 소비자 주의 4건:
 * 1. `stale=true` 는 재계산이 큐에 있다는 보장이 아니다(실패 쿨다운 60s 동안
 *    stale 을 보고하면서 자동 디스패치는 억제) → stale 은 "갱신 필요"까지만,
 *    "갱신 중"은 `snapshot_status` 로만.
 * 2. 이전 성공 뒤 실패한 행은 `failed` + `stale=false` → 실패를 알리면서도
 *    직전(유효) 후보는 계속 보여준다.
 * 3. `computed_at === null`(최초/부트스트랩)도 `stale=false` → 신선도·렌더 분기는
 *    `computed_at`/`snapshot_status` 로 한다.
 * 4. `evaluated_project_count` 는 스냅샷의 고정 분석 예산(250) 산물이고 요청
 *    limit 과 무관하다 → 라벨·각주를 그렇게 붙인다.
 */
export function CandidatesPreview({ pollIntervalMs }: CandidatesPreviewProps) {
  const { session } = useShellContext();
  const [highPriorityOnly, setHighPriorityOnly] = useState(false);
  const query = useStrategyCandidatesQuery(
    session,
    { limit: CANDIDATE_LIMIT, highPriorityOnly },
    null,
    { pollIntervalMs }
  );
  const refresh = useRefreshStrategyCandidatesMutation(session);
  const snapshot = query.data;

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div className="flex flex-col items-start gap-1">
          <CardTitle>영향 후보 미리보기</CardTitle>
          <SnapshotFreshnessBadge snapshot={snapshot} />
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => refresh.mutate({ highPriorityOnly })}
          disabled={refresh.isPending}
        >
          {refresh.isPending ? "요청 중" : "새로고침"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={highPriorityOnly}
            onChange={(event) => setHighPriorityOnly(event.target.checked)}
            className="h-4 w-4 accent-[var(--color-primary)]"
          />
          <span>우선순위 높음만</span>
        </label>
        {query.error ? (
          <p className="text-xs text-[var(--color-danger)]" role="alert">
            {query.error.message ?? "후보를 불러오지 못했습니다."}
          </p>
        ) : null}
        <SnapshotStatusNotice snapshot={snapshot} />
        {snapshot && hasComputedSnapshot(snapshot) ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-[var(--color-muted)]">{ANALYZED_LABEL}</span>
              <strong className="tabular-nums">{snapshot.evaluated_project_count}건</strong>
            </div>
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-[var(--color-muted)]">{MATCHED_LABEL}</span>
              <strong className="tabular-nums">{snapshot.returned_candidate_count}건</strong>
            </div>
            <p className="text-[11px] text-[var(--color-muted)]">{ANALYZED_HINT}</p>
            <CandidateList candidates={snapshot.candidates} session={session} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
