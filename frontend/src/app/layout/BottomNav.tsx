import { useNavigate } from "react-router-dom";
import type { RouteKey } from "@/shared/types";
import { cn } from "@/shared/lib";
import { ROUTE_LABELS } from "./Shell";

const bottomRoutes: RouteKey[] = ["home", "opportunities", "bids", "results"];

export function BottomNav({ route }: { route: RouteKey }) {
  const navigate = useNavigate();

  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-10 flex items-stretch justify-around border-t border-[var(--color-border)] bg-[var(--color-card)]/95 backdrop-blur"
      aria-label="대시보드 탭"
    >
      {bottomRoutes.map((routeKey) => {
        const Icon = ROUTE_LABELS[routeKey].icon;
        const active = route === routeKey;
        return (
          <button
            key={routeKey}
            type="button"
            onClick={() => navigate(ROUTE_LABELS[routeKey].path)}
            className={cn(
              "flex flex-1 flex-col items-center gap-1 px-2 py-3 text-xs",
              active
                ? "text-[var(--color-primary)]"
                : "text-[var(--color-muted)] hover:text-[var(--color-fg)]"
            )}
            aria-current={active ? "page" : undefined}
          >
            <Icon size={19} />
            <span>{ROUTE_LABELS[routeKey].label}</span>
          </button>
        );
      })}
    </nav>
  );
}
