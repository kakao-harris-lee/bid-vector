import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import type { DecisionRecommendationItem } from "@/shared/types/decisions";

export function RecommendationsList({
  recommendations,
  headline,
  loading,
  error
}: {
  recommendations: DecisionRecommendationItem[];
  headline?: string;
  loading: boolean;
  error: Error | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          추천
          {headline ? (
            <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">{headline}</span>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        {loading && recommendations.length === 0 ? (
          <p className="text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
        {error ? (
          <p className="text-[var(--color-danger)]" role="alert">
            {error.message ?? "추천을 불러오지 못했습니다."}
          </p>
        ) : null}
        {!loading && recommendations.length === 0 ? (
          <p className="text-[var(--color-muted)]">현재 권장 액션이 없습니다.</p>
        ) : null}
        {recommendations.map((item) => (
          <article
            key={item.key}
            className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
          >
            <div className="flex items-center justify-between gap-2">
              <strong className="text-[var(--color-fg)]">{item.title}</strong>
              <Badge tone={item.severity === "action" ? "watch" : item.severity === "info" ? "info" : "healthy"}>
                {item.severity}
              </Badge>
            </div>
            <p className="text-[var(--color-muted)]">{item.summary}</p>
            {item.suggested_adjustment ? (
              <p className="text-[var(--color-fg)]">제안: {item.suggested_adjustment}</p>
            ) : null}
          </article>
        ))}
      </CardContent>
    </Card>
  );
}
