import { useNavigate } from "react-router-dom";
import { ArrowRight, Info } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card";
import { Button } from "@/shared/components/ui/button";

import { GUIDE_STEPS } from "../guideContent";

export function AppWorkflowGuide() {
  const navigate = useNavigate();

  return (
    <div className="space-y-4">
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
        {GUIDE_STEPS.map((step) => (
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
