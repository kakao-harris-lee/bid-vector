import { ArrowLeft, Info } from "lucide-react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/components/ui";
import type {
  OnboardingFieldSuggestion,
  OnboardingSuggestionsResponse
} from "@/shared/api";
import { STEP_META } from "./constants";
import {
  acceptedCount,
  effectiveDecision,
  type DecisionMap,
  type DecisionState
} from "./decisions";
import { SuggestionCard } from "./SuggestionCard";

export interface ReviewStepProps {
  query: UseQueryResult<OnboardingSuggestionsResponse, Error>;
  overrides: DecisionMap;
  onDecision: (field: string, next: DecisionState) => void;
  onApply: () => void;
  onBack: () => void;
  applyPending: boolean;
  applyError?: string | null;
}

function SuggestionGroup({
  title,
  items,
  overrides,
  onDecision
}: {
  title: string;
  items: OnboardingFieldSuggestion[];
  overrides: DecisionMap;
  onDecision: (field: string, next: DecisionState) => void;
}) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-[var(--color-fg)]">{title}</h3>
      <ul className="flex flex-col gap-2">
        {items.map((item) => (
          <SuggestionCard
            key={item.field}
            suggestion={item}
            decision={effectiveDecision(item, overrides)}
            onDecision={onDecision}
          />
        ))}
      </ul>
    </section>
  );
}

export function ReviewStep({
  query,
  overrides,
  onDecision,
  onApply,
  onBack,
  applyPending,
  applyError
}: ReviewStepProps) {
  const meta = STEP_META.review;
  const data = query.data;
  const profile = data?.profile ?? [];
  const strategy = data?.strategy ?? [];
  const allSuggestions = [...profile, ...strategy];
  const accepted = acceptedCount(allSuggestions, overrides);
  const hasSuggestions = allSuggestions.length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{meta.title}</CardTitle>
        <CardDescription>{meta.description}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {query.isPending ? (
          <p className="text-sm text-[var(--color-muted)]">후보를 불러오는 중…</p>
        ) : null}

        {query.error ? (
          <p className="text-sm text-[var(--color-danger)]" role="alert">
            {query.error.message ?? "후보를 불러오지 못했습니다."}
          </p>
        ) : null}

        {data ? (
          <>
            <div
              className="flex items-start gap-2 rounded-md bg-[color-mix(in_oklch,var(--color-info),white_82%)] p-2 text-xs text-[var(--color-fg)]"
              role="note"
            >
              <Info size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
              <div className="flex flex-col gap-0.5">
                <span>{data.diagnostics}</span>
                <span className="text-[var(--color-muted)]">
                  매칭 공고 {data.matched_notice_count}건 · 아래 후보는 모두 <strong>확정 아님</strong>입니다. 수락한 값만 반영됩니다.
                </span>
              </div>
            </div>

            {hasSuggestions ? (
              <>
                <SuggestionGroup
                  title="회사 정보 후보"
                  items={profile}
                  overrides={overrides}
                  onDecision={onDecision}
                />
                <SuggestionGroup
                  title="감시 전략 후보"
                  items={strategy}
                  overrides={overrides}
                  onDecision={onDecision}
                />
              </>
            ) : (
              <p className="text-sm text-[var(--color-muted)]">
                추천할 후보가 없습니다. 키워드를 바꾸거나 지역/예산 힌트를 조정해 보세요.
              </p>
            )}

            {applyError ? (
              <p className="text-sm text-[var(--color-danger)]" role="alert">
                {applyError}
              </p>
            ) : null}
          </>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft size={14} aria-hidden="true" /> seed 다시 입력
          </Button>
          <Button
            onClick={onApply}
            disabled={accepted === 0 || applyPending}
          >
            {applyPending ? "반영 중…" : `수락한 ${accepted}건 반영`}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
