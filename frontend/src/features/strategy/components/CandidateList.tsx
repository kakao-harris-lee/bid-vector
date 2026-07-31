import type { BadgeTone } from "@/shared/components/ui";
import { Badge } from "@/shared/components/ui";
import { formatCurrencyCompact } from "@/shared/lib";
import type { AuthSession } from "@/app/layout/AuthGate";
import type {
  OperatorStrategyCandidateItem,
  StrategyAction
} from "@/shared/types/strategy";
import { EligibilityFeedbackButtons } from "../EligibilityFeedbackButtons";

const EMPTY_LABEL = "현재 매칭되는 후보가 없습니다.";
const ACTION_LABEL: Record<StrategyAction, string> = {
  bid_now: "투찰",
  review: "검토",
  skip: "보류"
};
const ACTION_TONE: Record<StrategyAction, BadgeTone> = {
  bid_now: "healthy",
  review: "watch",
  skip: "muted"
};

export interface CandidateListProps {
  candidates: OperatorStrategyCandidateItem[];
  session: AuthSession | null;
}

/**
 * 스냅샷 상위 후보 목록. **성공 계산이 있는 스냅샷에서만** 렌더된다 —
 * 부트스트랩 0건을 "매칭 후보 없음"으로 오도하지 않기 위해 호출부가
 * `hasComputedSnapshot` 으로 게이트한다(§2 정직 명세).
 */
export function CandidateList({ candidates, session }: CandidateListProps) {
  return (
    <ul className="flex flex-col gap-2 pt-2" aria-label="상위 후보">
      {candidates.length === 0 ? (
        <li className="text-xs text-[var(--color-muted)]">{EMPTY_LABEL}</li>
      ) : (
        candidates.map((candidate) => (
          <li
            key={candidate.project_id}
            className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2 text-xs"
          >
            <div className="flex items-center justify-between gap-2">
              <span
                className="truncate font-medium text-[var(--color-fg)]"
                title={candidate.title}
              >
                {candidate.title}
              </span>
              <Badge tone={ACTION_TONE[candidate.action]}>
                {ACTION_LABEL[candidate.action]}
              </Badge>
            </div>
            <div className="flex items-center justify-between text-[var(--color-muted)]">
              <span>{candidate.category ?? "-"}</span>
              <span className="tabular-nums">
                {formatCurrencyCompact(candidate.budget_estimate)}
              </span>
            </div>
            <EligibilityFeedbackButtons projectId={candidate.project_id} session={session} />
          </li>
        ))
      )}
    </ul>
  );
}
