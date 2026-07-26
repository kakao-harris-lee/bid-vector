import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
import type { SyntheticOperatorItem } from "@/shared/types/synthetic";

export function SeedPanel({
  operators,
  loading,
  onSeed,
  seedPending
}: {
  operators: SyntheticOperatorItem[];
  loading: boolean;
  onSeed: (purge: boolean) => void;
  seedPending: boolean;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>
          시드된 운영자
          <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
            ({operators.length}건)
          </span>
        </CardTitle>
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => onSeed(true)} disabled={seedPending}>
            리시드 (purge)
          </Button>
          <Button type="button" size="sm" onClick={() => onSeed(false)} disabled={seedPending}>
            시드
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {loading && operators.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">불러오는 중…</p>
        ) : operators.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">
            아직 시드되지 않았습니다. "시드" 버튼으로 12 아키타입을 upsert하세요.
          </p>
        ) : (
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3" aria-label="synthetic 운영자 카드">
            {operators.map((op) => (
              <li
                key={op.user_id}
                className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2 text-xs"
              >
                <div className="flex items-center justify-between">
                  <strong className="text-[var(--color-fg)]">{op.display_name}</strong>
                  <Badge tone="muted">{op.slug}</Badge>
                </div>
                <span className="text-[var(--color-muted)]">{op.company ?? "-"}</span>
                <span className="text-[var(--color-muted)]">
                  threshold {formatPercent(op.bid_now_threshold)} / {formatPercent(op.review_threshold)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
