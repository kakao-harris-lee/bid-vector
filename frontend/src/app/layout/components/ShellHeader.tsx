import { useNavigate } from "react-router-dom";
import {
  BarChart3,
  Beaker,
  Bell,
  BookOpen,
  Building2,
  ClipboardList,
  FlaskConical,
  FileSearch,
  HeartPulse,
  LogOut,
  RefreshCw,
  Sliders,
  Sparkles,
  Target
} from "lucide-react";
import { Badge } from "@/shared/components/ui";
import type { DashboardMetric } from "@/shared/types";
import { formatDate } from "@/shared/lib";
import { logoutSession } from "../AuthGate";
import { IconButton } from "../IconButton";
import { useActiveOperator } from "@/app/operatorContext";
import { OperatorSwitcher } from "../OperatorSwitcher";
import { toneFromStatus } from "../shellStatus";
import {
  ACCURACY_REPORT_ROUTE_PATH,
  DECISIONS_ROUTE_PATH,
  DECISION_SAMPLES_ROUTE_PATH,
  EXPERIMENTS_ROUTE_PATH,
  GUIDE_ROUTE_PATH,
  ONBOARDING_ROUTE_PATH,
  OPERATIONS_ROUTE_PATH,
  PROFILE_ROUTE_PATH,
  PROJECTS_ROUTE_PATH,
  STRATEGY_ROUTE_PATH,
  SYNTHETIC_ROUTE_PATH
} from "../shellRoutes";

export function ShellHeader({
  pageTitle,
  headerDate,
  homePath,
  isAdminSurface,
  activeOperator,
  operationalStatus,
  refreshing,
  pathname,
  onOpenNotifications,
  onRefresh
}: {
  pageTitle: string;
  headerDate: string;
  homePath: string;
  isAdminSurface: boolean;
  activeOperator: ReturnType<typeof useActiveOperator>;
  operationalStatus: DashboardMetric | null;
  refreshing: boolean;
  pathname: string;
  onOpenNotifications: () => void;
  onRefresh: () => void;
}) {
  const navigate = useNavigate();
  const onProfile = pathname.startsWith(PROFILE_ROUTE_PATH);
  const onStrategy = pathname.startsWith(STRATEGY_ROUTE_PATH);
  const onProjects = pathname.startsWith(PROJECTS_ROUTE_PATH);
  const onDecisions = pathname.startsWith(DECISIONS_ROUTE_PATH);
  const onExperiments = pathname.startsWith(EXPERIMENTS_ROUTE_PATH);
  const onSynthetic = pathname.startsWith(SYNTHETIC_ROUTE_PATH);
  const onOperations = pathname.startsWith(OPERATIONS_ROUTE_PATH);
  const onAccuracyReport = pathname.startsWith(ACCURACY_REPORT_ROUTE_PATH);
  const onDecisionSamples = pathname.startsWith(DECISION_SAMPLES_ROUTE_PATH);
  const onGuide = pathname.startsWith(GUIDE_ROUTE_PATH);
  const onOnboarding = pathname.startsWith(ONBOARDING_ROUTE_PATH);

  return (
    <header className="sticky top-0 z-10 flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-card)]/95 px-4 py-3 backdrop-blur">
      <button
        className="flex items-center gap-3 text-left text-sm focus-visible:outline-none"
        type="button"
        onClick={() => navigate(homePath)}
      >
        <span className="grid h-9 w-9 place-items-center rounded-md bg-[var(--color-primary)] text-xs font-bold text-[var(--color-primary-foreground)]">
          BV
        </span>
        <span className="flex flex-col">
          <strong className="text-base font-semibold">{pageTitle}</strong>
          <small className="text-xs text-[var(--color-muted)]">{formatDate(headerDate)}</small>
        </span>
      </button>
      <div className="flex items-center gap-2">
        {isAdminSurface ? <OperatorSwitcher activeOperator={activeOperator} /> : null}
        {isAdminSurface && operationalStatus ? (
          <Badge tone={toneFromStatus(operationalStatus.status)}>
            {operationalStatus.label}
          </Badge>
        ) : null}
        {isAdminSurface ? (
          <>
            <IconButton
              label="운영 대시보드"
              onClick={() => navigate(onOperations ? homePath : OPERATIONS_ROUTE_PATH)}
            >
              <HeartPulse size={18} />
            </IconButton>
            <IconButton
              label="가상 운영자 백테스트"
              onClick={() => navigate(onSynthetic ? homePath : SYNTHETIC_ROUTE_PATH)}
            >
              <FlaskConical size={18} />
            </IconButton>
            <IconButton
              label="실험 lifecycle"
              onClick={() => navigate(onExperiments ? homePath : EXPERIMENTS_ROUTE_PATH)}
            >
              <Beaker size={18} />
            </IconButton>
            <IconButton
              label="결정 게이트웨이"
              onClick={() => navigate(onDecisions ? homePath : DECISIONS_ROUTE_PATH)}
            >
              <BarChart3 size={18} />
            </IconButton>
            <IconButton
              label="정확도 리포트"
              onClick={() =>
                navigate(onAccuracyReport ? homePath : ACCURACY_REPORT_ROUTE_PATH)
              }
            >
              <Target size={18} />
            </IconButton>
            <IconButton
              label="의사결정 증적"
              onClick={() =>
                navigate(onDecisionSamples ? homePath : DECISION_SAMPLES_ROUTE_PATH)
              }
            >
              <ClipboardList size={18} />
            </IconButton>
          </>
        ) : (
          <>
            <IconButton
              label="사업자 온보딩"
              onClick={() => navigate(onOnboarding ? homePath : ONBOARDING_ROUTE_PATH)}
            >
              <Sparkles size={18} />
            </IconButton>
            <IconButton
              label="공고 탐색"
              onClick={() => navigate(onProjects ? homePath : PROJECTS_ROUTE_PATH)}
            >
              <FileSearch size={18} />
            </IconButton>
            <IconButton
              label="업체 정보"
              onClick={() => navigate(onProfile ? homePath : PROFILE_ROUTE_PATH)}
            >
              <Building2 size={18} />
            </IconButton>
            <IconButton
              label="전략 편집"
              onClick={() => navigate(onStrategy ? homePath : STRATEGY_ROUTE_PATH)}
            >
              <Sliders size={18} />
            </IconButton>
            <IconButton
              label="이용 가이드"
              onClick={() => navigate(onGuide ? homePath : GUIDE_ROUTE_PATH)}
            >
              <BookOpen size={18} />
            </IconButton>
          </>
        )}
        <IconButton
          label="알림"
          onClick={onOpenNotifications}
        >
          <Bell size={18} />
        </IconButton>
        <IconButton
          label="새로고침"
          onClick={onRefresh}
          disabled={refreshing}
        >
          <RefreshCw size={18} />
        </IconButton>
        <IconButton label="로그아웃" onClick={() => logoutSession()}>
          <LogOut size={18} />
        </IconButton>
      </div>
    </header>
  );
}
