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
        {/*
          확정 직후 기존 전략 candidates 재사용(설계 §UI 4단계). PR-B 이후 이 GET 은
          스냅샷 순수 읽기이고, 온보딩 apply 는 스냅샷 재계산을 디스패치하지 않으므로
          (services/onboarding/apply.py) **이 단계 진입의 첫 GET 이 자동 디스패치**를
          겸한다. 그래서 최초 진입은 CandidatesPreview 의 진행 UI(경과 안내)로
          대기하고, 계산이 끝나면 같은 카드가 목록으로 바뀐다(설계 §7·§9).
        */}
        <CandidatesPreview />
      </CardContent>
    </Card>
  );
}
