import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";

export function DecisionReviewFlowCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>투찰 전 확인 흐름</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 text-xs md:grid-cols-3">
        <ReviewFlowStep
          label="1. 목록에서 1차 확인"
          detail="추천가, 우선순위, 강점과 리스크 신호를 먼저 확인합니다."
        />
        <ReviewFlowStep
          label="2. 요약에서 가격 근거 확인"
          detail="예측 가격대, 하한율 참고값, 분야 통계를 함께 보고 가격 신호를 검토합니다."
        />
        <ReviewFlowStep
          label="3. 선택 사유 기록"
          detail="투찰·검토·보류 전환 전 내부 판단 사유를 남겨 나중에 과거 오차와 비교합니다."
        />
      </CardContent>
    </Card>
  );
}

function ReviewFlowStep({ label, detail }: { label: string; detail: string }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3">
      <p className="font-medium text-[var(--color-fg)]">{label}</p>
      <p className="mt-1 text-[var(--color-muted)]">{detail}</p>
    </div>
  );
}
