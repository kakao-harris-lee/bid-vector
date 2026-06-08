import { Info } from "lucide-react";

import { Card, CardContent } from "@/shared/components/ui/card";

import { CategoryRequirements } from "./CategoryRequirements";
import { KonepsProcessFlow } from "./KonepsProcessFlow";

export function KonepsProcessGuide() {
  return (
    <div className="space-y-4">
      <Card className="border-[var(--color-border)] bg-[var(--color-surface-muted)]">
        <CardContent className="flex items-start gap-3 p-4">
          <Info className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-text-muted)]" />
          <div className="space-y-1 text-sm">
            <p className="text-[var(--color-text-muted)]">
              나라장터 입찰은 자격 등록부터 개찰까지 정해진 절차를 따릅니다. 아래 흐름에서 ★ 표시는 이
              서비스가 돕는 단계입니다. 가입·인증·실제 투찰 제출은 나라장터에서 직접 진행합니다.
            </p>
            <p className="text-xs text-[var(--color-text-muted)]">
              참고: 결과 화면의 낙찰률은 실제 개찰 결과가 아니라 가격 기준 추정 프록시일 수 있습니다.
            </p>
          </div>
        </CardContent>
      </Card>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold tracking-tight">입찰 절차 흐름</h2>
        <KonepsProcessFlow />
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold tracking-tight">카테고리별 핵심 요약</h2>
        <CategoryRequirements />
      </section>
    </div>
  );
}
