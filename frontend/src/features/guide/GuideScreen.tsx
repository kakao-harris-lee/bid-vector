import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Info } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card";
import { Button } from "@/shared/components/ui/button";
import { Badge } from "@/shared/components/ui/badge";

export interface GuideScreenProps {
  className?: string;
}

interface GuideLink {
  label: string;
  path: string;
}

interface GuideStep {
  no: number;
  title: string;
  description: string;
  caveat?: string;
  links: GuideLink[];
}

const GUIDE_STEPS: GuideStep[] = [
  {
    no: 1,
    title: "전략 설정",
    description:
      "운영자 전략과 임계값을 먼저 설정합니다. 검토(review) 기준과 즉시투찰(bid_now) 기준을 정해 두면 이후 단계의 추천·결정이 이 기준을 따릅니다.",
    links: [{ label: "전략 편집 열기", path: "/dashboard/strategy" }],
  },
  {
    no: 2,
    title: "공고 수집 · 발굴",
    description:
      "나라장터(KONEPS) 공고를 수집한 뒤 입찰(=입찰 후보 발굴) 화면에서 추진할 만한 후보를 확인합니다. 수집 상태와 주기는 운영 · 수집 화면에서 점검합니다.",
    links: [
      { label: "운영 · 수집 열기", path: "/dashboard/operations" },
      { label: "입찰 후보 열기", path: "/dashboard/opportunities" },
    ],
  },
  {
    no: 3,
    title: "공고 분석",
    description:
      "관심 공고를 공고 탐색에서 자세히 봅니다. 분류 결과와 pgvector 기반 유사 공고로 경쟁 강도와 참고가(과거 유사 건 가격대)를 파악합니다.",
    links: [{ label: "공고 탐색 열기", path: "/dashboard/projects" }],
  },
  {
    no: 4,
    title: "가격 예측 & guardrail",
    description:
      "공고 상세에서 적정 투찰가 예측을 확인합니다. 카테고리 낙찰하한 미만의 추천은 guardrail이 자동으로 차단하므로, 하한 미만 가격은 추천·결정에 노출되지 않습니다.",
    links: [
      { label: "공고 상세 열기", path: "/dashboard/projects" },
      { label: "결정 게이트웨이 열기", path: "/dashboard/decisions" },
    ],
  },
  {
    no: 5,
    title: "투찰 결정",
    description:
      "결정 게이트웨이에서 review / bid_now 게이트로 각 건의 추진 여부를 판단합니다. 전략 임계값을 넘은 건만 즉시투찰 후보로 분류됩니다.",
    links: [{ label: "결정 게이트웨이 열기", path: "/dashboard/decisions" }],
  },
  {
    no: 6,
    title: "투찰 제출",
    description:
      "추진하기로 한 건의 투찰가를 확정하고 제출합니다. 투찰(=가격 제출) 화면에서 제출 상태를 관리합니다.",
    links: [{ label: "투찰 열기", path: "/dashboard/bids" }],
  },
  {
    no: 7,
    title: "결과 · 낙찰 확인",
    description:
      "결과 화면에서 낙찰 여부와 예측 오차(예측가 대비 실제 결과)를 확인합니다.",
    caveat:
      "표시되는 낙찰률/win rate는 가격 기준 추정치일 수 있습니다(실제 개찰 결과가 아니라 가격 비교 기반 추정). 해석 시 참고만 하세요.",
    links: [{ label: "결과 열기", path: "/dashboard/results" }],
  },
  {
    no: 8,
    title: "피드백 · 전략 튜닝",
    description:
      "결과를 바탕으로 전략 임계값을 보정하고, 합성 백테스트로 변경 효과를 검증한 뒤 다음 사이클에 반영합니다.",
    caveat:
      "백테스트의 낙찰률 또한 가격 기준 추정 프록시이므로, 절대치보다 전후 비교 추세로 보는 것이 안전합니다.",
    links: [
      { label: "전략 편집 열기", path: "/dashboard/strategy" },
      { label: "합성 백테스트 열기", path: "/dashboard/synthetic-backtest" },
    ],
  },
];

export function GuideScreen({ className }: GuideScreenProps) {
  const navigate = useNavigate();
  const steps = useMemo(() => GUIDE_STEPS, []);

  return (
    <div className={className ? `space-y-4 ${className}` : "space-y-4"}>
      <header className="space-y-2">
        <Badge tone="info">이용 가이드</Badge>
        <h1 className="text-lg font-semibold tracking-tight">입찰 워크플로우 안내</h1>
        <p className="text-sm text-[var(--color-text-muted)]">
          나라장터(KONEPS) 공고에서 낙찰 확률이 높은 건을 찾아 적정 투찰가로 추진하는 흐름을 단계별로
          안내합니다.
        </p>
      </header>

      <Card className="border-[var(--color-border)] bg-[var(--color-surface-muted)]">
        <CardContent className="flex items-start gap-3 p-4">
          <Info className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-text-muted)]" />
          <div className="space-y-1 text-sm">
            <p className="font-medium">단일 운영자 기준으로 동작합니다.</p>
            <p className="text-[var(--color-text-muted)]">
              이 서비스는 한 명의 운영자가 직접 공고를 발굴하고 투찰가를 결정해 추진하는 흐름을 가정합니다.
              아래 순서대로 진행하면서 각 단계의 버튼으로 해당 화면으로 바로 이동할 수 있습니다.
            </p>
          </div>
        </CardContent>
      </Card>

      <ol className="space-y-3">
        {steps.map((step) => (
          <li key={step.no}>
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center gap-3">
                  <span
                    aria-hidden
                    className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary)] text-xs font-semibold text-white"
                  >
                    {step.no}
                  </span>
                  <CardTitle className="text-base">{step.title}</CardTitle>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 pt-0">
                <p className="text-sm text-[var(--color-text-muted)]">{step.description}</p>
                {step.caveat ? (
                  <p className="rounded-md bg-[var(--color-surface-muted)] px-3 py-2 text-xs text-[var(--color-text-muted)]">
                    참고: {step.caveat}
                  </p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  {step.links.map((link) => (
                    <Button
                      key={link.path + link.label}
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => navigate(link.path)}
                    >
                      {link.label}
                      <ArrowRight className="h-4 w-4" />
                    </Button>
                  ))}
                </div>
              </CardContent>
            </Card>
          </li>
        ))}
      </ol>
    </div>
  );
}
