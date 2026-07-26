import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { formatPercent } from "@/shared/lib";
import type { StrategyFormValues } from "../schema";

export function StrategyDecisionGuide({ values }: { values: StrategyFormValues }) {
  const targetingCount =
    (values.focus_categories?.length ?? 0) +
    (values.focus_regions?.length ?? 0) +
    (values.required_keywords?.length ?? 0) +
    (values.exclude_regions?.length ?? 0) +
    (values.exclude_keywords?.length ?? 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>추천 판단 기준</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        <p className="text-[var(--color-muted)]">
          추천 후보는 대상 조건, 예산 범위, 공고 적합도, 가격 적합도(추정), 워크로드
          패널티를 통과한 공고만 남기는 방식으로 좁힙니다.
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          <LabeledStat
            variant="guide"
            label="대상 조건"
            value={`${targetingCount.toLocaleString("ko-KR")}개`}
            detail="중점·제외 지역과 키워드가 후보 폭을 조절합니다."
          />
          <LabeledStat
            variant="guide"
            label="예산 범위"
            value={`${formatBudget(values.min_budget_estimate)} ~ ${formatBudget(
              values.max_budget_estimate,
              "무제한"
            )}`}
            detail="공고 추정가격이 이 범위를 벗어나면 우선순위가 낮아집니다."
          />
          <LabeledStat
            variant="guide"
            label="점수 문턱"
            value={`공고 적합도 ${formatPercent(values.minimum_match_score)} 이상`}
            detail={`가격 적합도(추정) ${formatPercent(
              values.minimum_probability_score
            )} 이상`}
          />
          <LabeledStat
            variant="guide"
            label="액션 분기"
            value={`투찰 ${formatPercent(values.bid_now_threshold)} / 검토 ${formatPercent(
              values.review_threshold
            )}`}
            detail="검토 임계값 미만은 보류 후보로 분류됩니다."
          />
        </div>
        <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-secondary)] p-3">
          <div className="mb-1 flex items-center gap-2">
            <Badge tone="info">확인 흐름</Badge>
            <span className="font-medium text-[var(--color-fg)]">투찰 전 요약 확인</span>
          </div>
          <p className="text-[var(--color-muted)]">
            가격 적합도(추정)는 P(낙찰)이 아니라 추천가가 예측 적정대에 들어가는지
            보는 내부 신호입니다. 실제 투찰 전에는 투찰 요약에서 추천가, 예측 가격대,
            하한율 참고값, 분야 통계, 강점과 리스크를 함께 확인하세요.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function formatBudget(value: number, zeroLabel = "0원"): string {
  if (value === 0) return zeroLabel;
  return `${value.toLocaleString("ko-KR")}원`;
}
