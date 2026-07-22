import { ArrowRight, CheckCircle2, Slash } from "lucide-react";
import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/components/ui";
import type { OnboardingApplyResponse } from "@/shared/api";
import { fieldMeta, STEP_META } from "./constants";
import { SuggestionValueDisplay } from "./SuggestionValueEditor";

export interface ResultStepProps {
  result: OnboardingApplyResponse;
  onContinue: () => void;
}

export function ResultStep({ result, onContinue }: ResultStepProps) {
  const meta = STEP_META.result;
  const applied = result.applied ?? [];
  const ignored = result.ignored ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{meta.title}</CardTitle>
        <CardDescription>{meta.description}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <section className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 size={15} aria-hidden="true" className="text-[var(--color-success)]" />
            <h3 className="text-sm font-semibold">반영된 필드 {applied.length}건</h3>
          </div>
          {applied.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">반영된 필드가 없습니다.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {applied.map((item) => (
                <li
                  key={`${item.target}:${item.field}`}
                  className="flex flex-col gap-1 rounded-md border border-[var(--color-success)] bg-[var(--color-card)] p-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{fieldMeta(item.field).label}</span>
                    <Badge tone={item.target === "profile" ? "info" : "healthy"}>
                      {item.target === "profile" ? "회사 정보" : "감시 전략"}
                    </Badge>
                  </div>
                  <SuggestionValueDisplay field={item.field} value={item.value} />
                </li>
              ))}
            </ul>
          )}
        </section>

        {ignored.length > 0 ? (
          <section className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <Slash size={15} aria-hidden="true" className="text-[var(--color-muted)]" />
              <h3 className="text-sm font-semibold">무시된 필드 {ignored.length}건</h3>
            </div>
            <ul className="flex flex-col gap-1">
              {ignored.map((item) => (
                <li
                  key={item.field}
                  className="flex items-center justify-between gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 py-1.5 text-xs"
                >
                  <span className="font-medium">{fieldMeta(item.field).label}</span>
                  <span className="text-[var(--color-muted)]">{item.reason}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <Button onClick={onContinue} className="self-start">
          공고 미리보기 <ArrowRight size={15} aria-hidden="true" />
        </Button>
      </CardContent>
    </Card>
  );
}
