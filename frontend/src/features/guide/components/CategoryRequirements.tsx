import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card";

import { KONEPS_CATEGORIES, type KonepsCategory } from "../guideContent";

function CategorySection({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="space-y-1.5">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
        {label}
      </h4>
      <ul className="list-disc space-y-1 pl-4 text-sm text-[var(--color-fg)]">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function CategoryCard({ category }: { category: KonepsCategory }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{category.title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        <CategorySection label="자격요건" items={category.requirements} />
        <CategorySection label="낙찰방식" items={category.awardMethods} />
        <div className="space-y-1.5">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            우리 매핑
          </h4>
          <p className="rounded-md bg-[var(--color-surface-muted)] px-3 py-2 text-sm text-[var(--color-text-muted)]">
            {category.ourMapping}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

export function CategoryRequirements() {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {KONEPS_CATEGORIES.map((category) => (
        <CategoryCard key={category.key} category={category} />
      ))}
    </div>
  );
}
