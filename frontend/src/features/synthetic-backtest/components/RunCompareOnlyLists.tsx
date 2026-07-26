import { Badge } from "@/shared/components/ui";

export function RunCompareOnlyLists({
  onlyInA,
  onlyInB
}: {
  onlyInA: string[];
  onlyInB: string[];
}) {
  if (onlyInA.length === 0 && onlyInB.length === 0) return null;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <OnlyInList side="A" slugs={onlyInA} />
      <OnlyInList side="B" slugs={onlyInB} />
    </div>
  );
}

function OnlyInList({ side, slugs }: { side: "A" | "B"; slugs: string[] }) {
  return (
    <div
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
      aria-label={`런 ${side}에만 있는 회사`}
    >
      <p className="mb-1 font-medium text-[var(--color-fg)]">런 {side}에만 있는 회사</p>
      {slugs.length === 0 ? (
        <p className="text-[var(--color-muted)]">없음</p>
      ) : (
        <ul className="flex flex-wrap gap-1">
          {slugs.map((slug) => (
            <li key={slug}>
              <Badge tone={side === "A" ? "muted" : "info"}>{slug}</Badge>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
