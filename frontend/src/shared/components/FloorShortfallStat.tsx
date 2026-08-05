import { Badge } from "@/shared/components/ui";
import {
  FLOOR_SHORTFALL_TITLE,
  describeFloorShortfall
} from "@/shared/floorShortfall";
import type { FloorShortfallEstimate } from "@/shared/types/floorShortfall";

export interface FloorShortfallStatProps {
  estimate: FloorShortfallEstimate | null | undefined;
  label?: string;
}

/**
 * 하한 미달 빈도(과거 표본) 표시 — 판정 불가를 0% 와 절대 합치지 않는 자리.
 *
 * 표시할 내용은 `describeFloorShortfall`(순수 함수)이 정하고 여기서는 렌더만 한다.
 * 두 상태의 뱃지 쓰임이 다르다:
 *
 * - 측정됨: 숫자 문장(`strong`) **옆에** 빈도 밴드 톤 뱃지를 붙여, 숫자를 읽지 않아도
 *   빈도가 높은 쪽인지 색으로 보이게 한다.
 * - 판정 불가·미산출: 숫자 자리 자체를 muted 뱃지로 채운다. 숫자가 없는 자리라
 *   측정된 낮은 빈도처럼 읽히면 안 된다.
 */
export function FloorShortfallStat({ estimate, label }: FloorShortfallStatProps) {
  const display = describeFloorShortfall(estimate);

  return (
    <div className="flex flex-col gap-0.5" data-shortfall-kind={display.kind}>
      <span className="text-xs text-[var(--color-muted)]">
        {label ?? FLOOR_SHORTFALL_TITLE}
      </span>
      <span className="flex flex-wrap items-center gap-2">
        {display.kind === "measured" ? (
          <>
            <strong className="text-sm tabular-nums text-[var(--color-fg)]">
              {display.headline}
            </strong>
            {display.badgeLabel ? (
              <Badge tone={display.tone}>{display.badgeLabel}</Badge>
            ) : null}
          </>
        ) : (
          /* 판정 불가·미산출은 숫자 자리에 숫자를 놓지 않는다 — 낮은 빈도로 읽히면 안 된다. */
          <Badge tone={display.tone}>{display.headline}</Badge>
        )}
      </span>
      {display.detail ? (
        <span className="text-[11px] leading-tight text-[var(--color-muted)]">
          {display.detail}
        </span>
      ) : null}
      <span className="text-[11px] leading-tight text-[var(--color-muted)]">
        {display.note}
      </span>
    </div>
  );
}
