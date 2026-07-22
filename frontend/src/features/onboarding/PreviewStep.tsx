import { RefreshCw } from "lucide-react";
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/components/ui";
// 직접 파일 import — barrel(`@/features/strategy`)은 무거운 StrategyEditor 를 온보딩
// 청크로 끌어오므로 CandidatesPreview 파일만 재사용한다(router.tsx 의 BidSummary 패턴).
import { CandidatesPreview } from "@/features/strategy/CandidatesPreview";
import { STEP_META } from "./constants";

export interface PreviewStepProps {
  onRestart: () => void;
}

export function PreviewStep({ onRestart }: PreviewStepProps) {
  const meta = STEP_META.preview;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div className="flex flex-col gap-1">
          <CardTitle>{meta.title}</CardTitle>
          <CardDescription>{meta.description}</CardDescription>
        </div>
        <Button variant="ghost" size="sm" onClick={onRestart} className="shrink-0">
          <RefreshCw size={14} aria-hidden="true" /> 처음부터
        </Button>
      </CardHeader>
      <CardContent>
        {/* 확정 직후 기존 전략 candidates 재사용(설계 §UI 4단계). */}
        <CandidatesPreview />
      </CardContent>
    </Card>
  );
}
