import { Fragment } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, ChevronDown, Sparkles } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card";
import { Button } from "@/shared/components/ui/button";
import { Badge } from "@/shared/components/ui/badge";

import { KONEPS_PROCESS_STEPS } from "../guideContent";

export function KonepsProcessFlow() {
  const navigate = useNavigate();

  return (
    <ol className="space-y-0">
      {KONEPS_PROCESS_STEPS.map((step, index) => (
        <Fragment key={step.no}>
          <li>
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <span
                      aria-hidden
                      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary)] text-xs font-semibold text-white"
                    >
                      {step.no}
                    </span>
                    <CardTitle className="text-base">{step.title}</CardTitle>
                  </div>
                  {step.ourHelp ? (
                    <Badge tone="info" className="shrink-0">
                      <Sparkles className="h-3 w-3" aria-hidden />★ 우리 도움
                    </Badge>
                  ) : (
                    <Badge tone="muted" className="shrink-0">
                      나라장터에서 진행
                    </Badge>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-3 pt-0">
                <p className="text-sm text-[var(--color-text-muted)]">{step.summary}</p>
                {step.ourHelp ? (
                  <p className="rounded-md border border-[color-mix(in_oklch,var(--color-info),white_60%)] bg-[color-mix(in_oklch,var(--color-info),white_85%)] px-3 py-2 text-sm text-[var(--color-fg)]">
                    <span className="font-medium">★ 우리 도움 </span>
                    {step.ourHelp}
                  </p>
                ) : null}
                {step.links.length > 0 ? (
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
                ) : null}
              </CardContent>
            </Card>
          </li>
          {index < KONEPS_PROCESS_STEPS.length - 1 ? (
            <li aria-hidden className="flex justify-center py-1">
              <ChevronDown className="h-5 w-5 text-[var(--color-text-muted)]" />
            </li>
          ) : null}
        </Fragment>
      ))}
    </ol>
  );
}
