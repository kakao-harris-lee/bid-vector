import type { DashboardSummaryResponse, RouteKey } from "@/shared/types";

export function SegmentedTabs({
  active,
  onChange,
  sections
}: {
  active: RouteKey;
  onChange: (route: RouteKey) => void;
  sections: DashboardSummaryResponse["sections"];
}) {
  return (
    <div className="segmented-tabs" role="tablist" aria-label="요약 탭">
      {sections.map((section) => {
        const route = (section.key === "opportunities" ? "opportunities" : section.key) as RouteKey;
        return (
          <button
            key={section.key}
            className={active === route ? "active" : ""}
            type="button"
            onClick={() => onChange(route)}
            role="tab"
            aria-selected={active === route}
          >
            <span>{section.label}</span>
            <strong>{section.count}</strong>
          </button>
        );
      })}
    </div>
  );
}
