import { useState } from "react";

import { Badge } from "@/shared/components/ui/badge";
import { cn } from "@/shared/lib/cn";

import { AppWorkflowGuide, KonepsProcessGuide } from "./components";

export interface GuideScreenProps {
  className?: string;
}

type TabKey = "koneps" | "app";

interface TabDef {
  key: TabKey;
  label: string;
  subtitle: string;
}

const TABS: TabDef[] = [
  {
    key: "koneps",
    label: "나라장터 입찰 절차",
    subtitle:
      "나라장터(KONEPS)의 실제 입찰 절차와 카테고리별 자격·낙찰방식을 정리하고, 각 단계에서 이 서비스가 돕는 부분을 표시합니다.",
  },
  {
    key: "app",
    label: "이 앱 사용 흐름",
    subtitle:
      "이 서비스로 공고를 발굴해 투찰가를 결정하고 결과를 피드백하는 운영 워크플로를 단계별로 안내합니다.",
  },
];

export function GuideScreen({ className }: GuideScreenProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("koneps");
  const activeDef = TABS.find((tab) => tab.key === activeTab) ?? TABS[0];

  return (
    <div className={className ? `space-y-4 ${className}` : "space-y-4"}>
      <header className="space-y-2">
        <Badge tone="info">이용 가이드</Badge>
        <h1 className="text-lg font-semibold tracking-tight">입찰 워크플로우 안내</h1>
        <p className="text-sm text-[var(--color-text-muted)]">{activeDef.subtitle}</p>
      </header>

      <div
        role="tablist"
        aria-label="가이드 탭"
        className="inline-flex w-full max-w-md gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-muted)] p-1"
      >
        {TABS.map((tab) => {
          const isActive = tab.key === activeTab;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              id={`guide-tab-${tab.key}`}
              aria-selected={isActive}
              aria-controls={`guide-panel-${tab.key}`}
              onClick={() => setActiveTab(tab.key)}
              className={cn(
                "flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]",
                isActive
                  ? "bg-[var(--color-card)] text-[var(--color-fg)] shadow-sm"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-fg)]",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {activeTab === "koneps" ? (
        <div
          role="tabpanel"
          id="guide-panel-koneps"
          aria-labelledby="guide-tab-koneps"
          tabIndex={0}
        >
          <KonepsProcessGuide />
        </div>
      ) : (
        <div role="tabpanel" id="guide-panel-app" aria-labelledby="guide-tab-app" tabIndex={0}>
          <AppWorkflowGuide />
        </div>
      )}
    </div>
  );
}
