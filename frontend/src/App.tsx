import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  Gavel,
  ListChecks,
  LogOut,
  RefreshCw,
  Send,
  Trophy,
  X
} from "lucide-react";
import {
  ApiError,
  clearSession,
  fetchBids,
  fetchDashboardSummary,
  fetchOpportunities,
  fetchPaperBiddingSummary,
  fetchResults,
  getStoredToken,
  getStoredUsername,
  login,
  resetPassword,
  storeSession
} from "./api";
import type {
  DashboardBidItem,
  DashboardMetric,
  DashboardOpportunityItem,
  DashboardResultItem,
  DashboardStatus,
  DashboardSummaryResponse,
  DashboardWorkItem,
  ListItem,
  PaperBiddingSettlementOverview,
  PaperBiddingSummaryResponse,
  RouteKey
} from "./types";
import "./styles.css";

type DetailSelection =
  | { kind: "opportunity"; item: DashboardOpportunityItem }
  | { kind: "bid"; item: DashboardBidItem }
  | { kind: "result"; item: DashboardResultItem };

const routeConfig: Record<RouteKey, { path: string; label: string; icon: typeof Gavel }> = {
  home: { path: "/dashboard", label: "오늘", icon: ListChecks },
  opportunities: { path: "/dashboard/opportunities", label: "입찰", icon: Gavel },
  bids: { path: "/dashboard/bids", label: "투찰", icon: Send },
  results: { path: "/dashboard/results", label: "결과", icon: Trophy }
};

const bottomRoutes: RouteKey[] = ["opportunities", "bids", "results"];

function routeFromPath(pathname: string): RouteKey {
  if (pathname.startsWith("/dashboard/bids")) return "bids";
  if (pathname.startsWith("/dashboard/opportunities")) return "opportunities";
  if (pathname.startsWith("/dashboard/results")) return "results";
  return "home";
}

function App() {
  const [token, setToken] = useState<string | null>(() => getStoredToken());
  const [username, setUsername] = useState<string | null>(() => getStoredUsername());
  const [route, setRoute] = useState<RouteKey>(() => routeFromPath(window.location.pathname));
  const [summary, setSummary] = useState<DashboardSummaryResponse | null>(null);
  const [opportunities, setOpportunities] = useState<DashboardOpportunityItem[]>([]);
  const [bids, setBids] = useState<DashboardBidItem[]>([]);
  const [results, setResults] = useState<DashboardResultItem[]>([]);
  const [paperSummary, setPaperSummary] = useState<PaperBiddingSummaryResponse | null>(null);
  const [activePreview, setActivePreview] = useState<RouteKey>("opportunities");
  const [selected, setSelected] = useState<DetailSelection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onPopState = () => setRoute(routeFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((nextRoute: RouteKey) => {
    const path = routeConfig[nextRoute].path;
    window.history.pushState({}, "", path);
    setRoute(nextRoute);
    setSelected(null);
  }, []);

  const handleUnauthorized = useCallback(() => {
    clearSession();
    setToken(null);
    setUsername(null);
    setSummary(null);
  }, []);

  const loadData = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const nextSummary = await fetchDashboardSummary(token);
      setSummary(nextSummary);

      if (route === "opportunities") {
        const response = await fetchOpportunities(token);
        setOpportunities(response.items);
      } else if (route === "bids") {
        const response = await fetchBids(token);
        setBids(response.items);
      } else if (route === "results") {
        const [response, nextPaperSummary] = await Promise.all([fetchResults(token), fetchPaperBiddingSummary(token)]);
        setResults(response.items);
        setPaperSummary(nextPaperSummary);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : "대시보드를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [handleUnauthorized, route, token]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const onLogin = async (loginUsername: string, password: string) => {
    const session = await login(loginUsername, password);
    storeSession(session);
    setToken(session.access_token);
    setUsername(session.username ?? loginUsername);
  };

  const onPasswordReset = async (loginUsername: string, resetToken: string, newPassword: string) => {
    const session = await resetPassword(loginUsername, resetToken, newPassword);
    storeSession(session);
    setToken(session.access_token);
    setUsername(session.username ?? loginUsername);
  };

  const logout = () => {
    clearSession();
    setToken(null);
    setUsername(null);
    setSummary(null);
  };

  if (!token) {
    return <LoginScreen onLogin={onLogin} onPasswordReset={onPasswordReset} />;
  }

  const currentDate = summary?.today ?? new Date().toISOString();
  const pageTitle = route === "home" ? "오늘 할 일" : routeConfig[route].label;

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand-button" type="button" onClick={() => navigate("home")}>
          <span className="brand-mark">BV</span>
          <span>
            <strong>{pageTitle}</strong>
            <small>{formatDate(currentDate)}</small>
          </span>
        </button>
        <div className="top-actions">
          {summary ? <StatusBadge status={summary.operational_status.status} label={summary.operational_status.label} /> : null}
          <IconButton label="새로고침" onClick={() => void loadData()} disabled={loading}>
            <RefreshCw size={18} />
          </IconButton>
          <IconButton label="로그아웃" onClick={logout}>
            <LogOut size={18} />
          </IconButton>
        </div>
      </header>

      <main className="content">
        {error ? <InlineNotice status="critical" message={error} /> : null}
        {loading && !summary ? <LoadingState /> : null}
        {!loading && !summary ? <EmptyState title="대시보드 데이터 없음" detail="표시할 데이터가 없습니다." /> : null}
        {summary ? (
          route === "home" ? (
            <HomeView
              summary={summary}
              activePreview={activePreview}
              onPreviewChange={setActivePreview}
              onNavigate={navigate}
              onSelect={setSelected}
            />
          ) : (
            <ListView
              route={route}
              loading={loading}
              summary={summary}
              opportunities={opportunities}
              bids={bids}
              results={results}
              paperSummary={paperSummary}
              onSelect={setSelected}
            />
          )
        ) : null}
      </main>

      <BottomNav route={route} onNavigate={navigate} />
      <DetailDrawer selection={selected} onClose={() => setSelected(null)} username={username} />
    </div>
  );
}

function LoginScreen({
  onLogin,
  onPasswordReset
}: {
  onLogin: (username: string, password: string) => Promise<void>;
  onPasswordReset: (username: string, resetToken: string, newPassword: string) => Promise<void>;
}) {
  const [mode, setMode] = useState<"login" | "reset">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [resetToken, setResetToken] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      if (mode === "reset") {
        if (newPassword !== confirmPassword) {
          setError("새 비밀번호가 일치하지 않습니다.");
          return;
        }
        await onPasswordReset(username, resetToken, newPassword);
      } else {
        await onLogin(username, password);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : mode === "reset" ? "비밀번호 초기화에 실패했습니다." : "로그인에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const switchMode = () => {
    setMode((current) => (current === "login" ? "reset" : "login"));
    setError(null);
    setPassword("");
    setResetToken("");
    setNewPassword("");
    setConfirmPassword("");
  };

  const submitDisabled =
    loading ||
    !username ||
    (mode === "login" ? !password : !resetToken || !newPassword || !confirmPassword);

  return (
    <main className="login-screen">
      <form className="login-panel" onSubmit={submit}>
        <div className="login-heading">
          <span className="brand-mark">BV</span>
          <div>
            <h1>입찰 대시보드</h1>
            <p>{mode === "login" ? "운영자 로그인" : "비밀번호 초기화"}</p>
          </div>
        </div>
        <label>
          아이디
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
        </label>
        {mode === "login" ? (
          <label>
            비밀번호
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
            />
          </label>
        ) : (
          <>
            <label>
              초기화 토큰
              <input
                value={resetToken}
                onChange={(event) => setResetToken(event.target.value)}
                type="password"
                autoComplete="one-time-code"
              />
            </label>
            <label>
              새 비밀번호
              <input
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                type="password"
                autoComplete="new-password"
              />
            </label>
            <label>
              새 비밀번호 확인
              <input
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                type="password"
                autoComplete="new-password"
              />
            </label>
          </>
        )}
        {error ? <InlineNotice status="critical" message={error} /> : null}
        <div className="login-actions">
          <button className="primary-button" type="submit" disabled={submitDisabled}>
            {loading ? "처리 중" : mode === "login" ? "로그인" : "비밀번호 초기화"}
          </button>
          <button className="secondary-button" type="button" onClick={switchMode} disabled={loading}>
            {mode === "login" ? "비밀번호 초기화" : "로그인으로 돌아가기"}
          </button>
        </div>
      </form>
    </main>
  );
}

function HomeView({
  summary,
  activePreview,
  onPreviewChange,
  onNavigate,
  onSelect
}: {
  summary: DashboardSummaryResponse;
  activePreview: RouteKey;
  onPreviewChange: (route: RouteKey) => void;
  onNavigate: (route: RouteKey) => void;
  onSelect: (selection: DetailSelection) => void;
}) {
  const previewItems = useMemo(() => {
    if (activePreview === "bids") return summary.recent_bids;
    if (activePreview === "results") return summary.recent_results;
    return summary.recent_opportunities;
  }, [activePreview, summary]);

  return (
    <>
      <section className="work-section">
        <SectionHeader title="오늘 할 일" count={summary.work_items.length} />
        {summary.work_items.length ? (
          <div className="work-list">
            {summary.work_items.map((item) => (
              <WorkItemCard key={item.key} item={item} onOpen={() => onNavigate(routeFromWorkItem(item))} />
            ))}
          </div>
        ) : (
          <EmptyState title="오늘 할 일 없음" detail="긴급 항목이 없습니다." />
        )}
      </section>

      <section className="metric-strip" aria-label="핵심 지표">
        {summary.metrics.map((metric) => (
          <MetricTile key={metric.key} metric={metric} />
        ))}
      </section>

      <section>
        <SegmentedTabs active={activePreview} onChange={onPreviewChange} sections={summary.sections} />
        <ItemList route={activePreview} items={previewItems} onSelect={onSelect} compact />
      </section>
    </>
  );
}

function ListView({
  route,
  loading,
  summary,
  opportunities,
  bids,
  results,
  paperSummary,
  onSelect
}: {
  route: RouteKey;
  loading: boolean;
  summary: DashboardSummaryResponse;
  opportunities: DashboardOpportunityItem[];
  bids: DashboardBidItem[];
  results: DashboardResultItem[];
  paperSummary: PaperBiddingSummaryResponse | null;
  onSelect: (selection: DetailSelection) => void;
}) {
  const items = route === "bids" ? bids : route === "results" ? results : opportunities;
  const fallbackItems = route === "bids" ? summary.recent_bids : route === "results" ? summary.recent_results : summary.recent_opportunities;
  const displayItems = items.length ? items : fallbackItems;

  return (
    <section>
      <SectionHeader title={routeConfig[route].label} count={displayItems.length} />
      {route === "results" ? <BacktestSummaryPanel summary={paperSummary} /> : null}
      {loading && !displayItems.length ? <LoadingState /> : <ItemList route={route} items={displayItems} onSelect={onSelect} />}
    </section>
  );
}

function BacktestSummaryPanel({ summary }: { summary: PaperBiddingSummaryResponse | null }) {
  const latestRun = summary?.latest_run;
  if (!latestRun) {
    return (
      <div className="backtest-panel">
        <div>
          <span>페이퍼 검증</span>
          <strong>실행 없음</strong>
        </div>
        <p>저장된 백테스트나 forward paper 실행이 없습니다.</p>
      </div>
    );
  }

  const averageBidRateError = numberFromSummary(latestRun.summary.average_absolute_bid_rate_error);
  const closeCount = numberFromSummary(latestRun.summary.within_0_3pct_count);
  const settlementOverview = latestRun.settlement_overview;
  const hasSettledResults = latestRun.settled_count > 0;
  return (
    <div className="backtest-panel">
      <div className="backtest-head">
        <div>
          <span>{latestRun.mode === "forward_paper" ? "Forward Paper" : "Historical Backtest"}</span>
          <strong>{latestRun.paper_bid_count}건 검증</strong>
        </div>
        <StatusBadge status={latestRun.status === "completed" ? "healthy" : latestRun.status === "failed" ? "critical" : "watch"} label={latestRun.status} />
      </div>
      <div className="backtest-grid">
        <div>
          <span>후보</span>
          <strong>{latestRun.candidate_count}</strong>
        </div>
        <div>
          <span>정산</span>
          <strong>{latestRun.settled_count}/{latestRun.paper_bid_count}</strong>
        </div>
        <div>
          <span>0.3% 이내</span>
          <strong>{hasSettledResults ? closeCount ?? 0 : "정산 없음"}</strong>
        </div>
        <div>
          <span>평균 오차</span>
          <strong>{hasSettledResults ? formatPercent(averageBidRateError) : "정산 없음"}</strong>
        </div>
      </div>
      {settlementOverview ? <SettlementOverview overview={settlementOverview} /> : null}
    </div>
  );
}

function SettlementOverview({ overview }: { overview: PaperBiddingSettlementOverview }) {
  return (
    <div className="settlement-overview">
      <div className="settlement-overview-head">
        <div>
          <span>승패 확정</span>
          <strong>{overview.label}</strong>
        </div>
        <StatusBadge status={statusFromSettlement(overview.status)} label={overview.label} />
      </div>
      <p>{overview.detail}</p>
      <div className="settlement-overview-meta">
        <span>{formatSettlementMilestone(overview)}</span>
        <span>
          대기 {overview.unsettled_count}건 · 결과 입수 {overview.ready_to_settle_count}건 · 마감 전 {overview.before_deadline_count}건
        </span>
      </div>
    </div>
  );
}

function ItemList({
  route,
  items,
  onSelect,
  compact = false
}: {
  route: RouteKey;
  items: ListItem[];
  onSelect: (selection: DetailSelection) => void;
  compact?: boolean;
}) {
  if (!items.length) {
    const emptyState = emptyStateForRoute(route);
    return <EmptyState title={emptyState.title} detail={emptyState.detail} />;
  }

  return (
    <div className={compact ? "list compact" : "list"}>
      {items.map((item) => {
        if (route === "bids" && "bid_id" in item) {
          return <BidRow key={item.bid_id} item={item} onSelect={() => onSelect({ kind: "bid", item })} />;
        }
        if (route === "results" && "tender_result_id" in item) {
          return <ResultRow key={item.tender_result_id} item={item} onSelect={() => onSelect({ kind: "result", item })} />;
        }
        if ("action" in item && !("bid_id" in item)) {
          return (
            <OpportunityRow
              key={`${item.source}:${item.decision_record_id ?? item.paper_bid_id}`}
              item={item}
              onSelect={() => onSelect({ kind: "opportunity", item })}
            />
          );
        }
        return null;
      })}
    </div>
  );
}

function OpportunityRow({ item, onSelect }: { item: DashboardOpportunityItem; onSelect: () => void }) {
  return (
    <button className="data-row" type="button" onClick={onSelect}>
      <div className="row-main">
        <div className="row-title">
          <span>{item.project.title}</span>
          <StatusBadge status={statusFromDecision(item.decision_status)} label={labelDecisionStatus(item.decision_status)} />
        </div>
        <p>{item.source_label} · {item.project.issuing_agency ?? item.project.category ?? "입찰 후보"}</p>
      </div>
      <div className="row-side">
        <MiniBar value={item.priority_score} />
        <strong>{formatCurrency(item.recommended_amount)}</strong>
        <small>{formatHours(item.deadline_hours_remaining)}</small>
      </div>
    </button>
  );
}

function BidRow({ item, onSelect }: { item: DashboardBidItem; onSelect: () => void }) {
  return (
    <button className="data-row" type="button" onClick={onSelect}>
      <div className="row-main">
        <div className="row-title">
          <span>{item.project.title}</span>
          <StatusBadge status={statusFromBid(item.status)} label={labelBidStatus(item.status)} />
        </div>
        <p>{formatDate(item.submitted_at)} 제출</p>
      </div>
      <div className="row-side">
        <MiniDonut value={item.score ?? 0.64} />
        <strong>{formatCurrency(item.bid_amount)}</strong>
        <small>{item.decision_status ? labelDecisionStatus(item.decision_status) : "판단 없음"}</small>
      </div>
    </button>
  );
}

function ResultRow({ item, onSelect }: { item: DashboardResultItem; onSelect: () => void }) {
  const errorRate = item.recommendation_error_rate ?? item.prediction_error_rate ?? 0;
  return (
    <button className="data-row" type="button" onClick={onSelect}>
      <div className="row-main">
        <div className="row-title">
          <span>{item.project.title}</span>
          <StatusBadge status={statusFromOutcome(item.award_outcome)} label={labelOutcome(item.award_outcome)} />
        </div>
        <p>{item.winning_company ?? item.result_status}</p>
      </div>
      <div className="row-side">
        <MiniBar value={Math.min(errorRate * 10, 1)} tone="amber" />
        <strong>{formatCurrency(item.winning_amount)}</strong>
        <small>오차 {formatPercent(errorRate)}</small>
      </div>
    </button>
  );
}

function emptyStateForRoute(route: RouteKey): { title: string; detail: string } {
  if (route === "opportunities") {
    return {
      title: "입찰 후보 없음",
      detail: "저장된 입찰 판단이나 최신 페이퍼 후보가 없습니다."
    };
  }
  if (route === "bids") {
    return {
      title: "실제 투찰 기록 없음",
      detail: "제출되었거나 검토 중인 실제 투찰 기록이 없습니다."
    };
  }
  if (route === "results") {
    return {
      title: "결과 없음",
      detail: "최종 낙찰 결과가 수집된 항목이 없습니다."
    };
  }
  return { title: "목록 없음", detail: "표시할 항목이 없습니다." };
}

function MetricTile({ metric }: { metric: DashboardMetric }) {
  return (
    <article className="metric-tile">
      <div>
        <span>{metric.label}</span>
        <strong>{formatMetricValue(metric)}</strong>
      </div>
      <StatusIcon status={metric.status} />
    </article>
  );
}

function WorkItemCard({ item, onOpen }: { item: DashboardWorkItem; onOpen: () => void }) {
  return (
    <button className="work-card" type="button" onClick={onOpen}>
      <div className="work-icon">
        {item.item_type === "opportunity_due" ? <Clock3 size={18} /> : null}
        {item.item_type === "bid_pending_result" ? <Send size={18} /> : null}
        {item.item_type === "result_review" ? <BarChart3 size={18} /> : null}
      </div>
      <div>
        <strong>{item.title}</strong>
        <span>{item.subtitle}</span>
      </div>
      <StatusBadge status={item.severity} label={labelStatus(item.status)} />
    </button>
  );
}

function SegmentedTabs({
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
        const route = section.key === "opportunities" ? "opportunities" : section.key;
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

function BottomNav({ route, onNavigate }: { route: RouteKey; onNavigate: (route: RouteKey) => void }) {
  return (
    <nav className="bottom-nav" aria-label="대시보드 탭">
      {bottomRoutes.map((routeKey) => {
        const Icon = routeConfig[routeKey].icon;
        const active = route === routeKey;
        return (
          <button key={routeKey} className={active ? "active" : ""} type="button" onClick={() => onNavigate(routeKey)}>
            <Icon size={19} />
            <span>{routeConfig[routeKey].label}</span>
          </button>
        );
      })}
    </nav>
  );
}

function DetailDrawer({
  selection,
  onClose,
  username
}: {
  selection: DetailSelection | null;
  onClose: () => void;
  username: string | null;
}) {
  if (!selection) return null;

  const project = selection.item.project;
  return (
    <aside className="detail-drawer" aria-label="상세">
      <div className="drawer-head">
        <div>
          <span>{username ?? "operator"}</span>
          <h2>{project.title}</h2>
        </div>
        <IconButton label="닫기" onClick={onClose}>
          <X size={18} />
        </IconButton>
      </div>
      <dl className="detail-grid">
        <div>
          <dt>기관</dt>
          <dd>{project.issuing_agency ?? project.demand_agency ?? "-"}</dd>
        </div>
        <div>
          <dt>예산</dt>
          <dd>{formatCurrency(project.budget_estimate)}</dd>
        </div>
        <div>
          <dt>마감</dt>
          <dd>{project.deadline ? formatDateTime(project.deadline) : "-"}</dd>
        </div>
        {"recommended_amount" in selection.item && selection.item.recommended_amount ? (
          <div>
            <dt>추천가</dt>
            <dd>{formatCurrency(selection.item.recommended_amount)}</dd>
          </div>
        ) : null}
        {"source_label" in selection.item ? (
          <div>
            <dt>출처</dt>
            <dd>{selection.item.source_label}</dd>
          </div>
        ) : null}
        {"bid_amount" in selection.item ? (
          <div>
            <dt>투찰가</dt>
            <dd>{formatCurrency(selection.item.bid_amount)}</dd>
          </div>
        ) : null}
        {"winning_amount" in selection.item ? (
          <>
            <div>
              <dt>낙찰가</dt>
              <dd>{formatCurrency(selection.item.winning_amount)}</dd>
            </div>
            <div>
              <dt>추천 오차</dt>
              <dd>{formatPercent(selection.item.recommendation_error_rate)}</dd>
            </div>
          </>
        ) : null}
      </dl>
      {"reasoning" in selection.item && selection.item.reasoning ? <p className="drawer-note">{selection.item.reasoning}</p> : null}
    </aside>
  );
}

function IconButton({
  label,
  onClick,
  disabled = false,
  children
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button className="icon-button" type="button" aria-label={label} title={label} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

function StatusBadge({ status, label }: { status: DashboardStatus | "critical" | "watch" | "info"; label: string }) {
  return <span className={`status-badge ${status}`}>{label}</span>;
}

function StatusIcon({ status }: { status: DashboardStatus }) {
  if (status === "healthy") return <CheckCircle2 className="status-icon healthy" size={20} />;
  if (status === "critical") return <AlertTriangle className="status-icon critical" size={20} />;
  if (status === "watch") return <Clock3 className="status-icon watch" size={20} />;
  return <Activity className="status-icon info" size={20} />;
}

function SectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="section-header">
      <h2>{title}</h2>
      <span>{count}</span>
    </div>
  );
}

function InlineNotice({ status, message }: { status: "critical" | "watch" | "info"; message: string }) {
  return (
    <div className={`inline-notice ${status}`} role="alert">
      {message}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="loading-state">
      <RefreshCw size={18} />
      <span>불러오는 중</span>
    </div>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <ClipboardCheck size={20} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function MiniBar({ value, tone = "teal" }: { value: number; tone?: "teal" | "amber" }) {
  const safeValue = Math.max(0, Math.min(1, value || 0));
  return (
    <span className={`mini-bar ${tone}`} aria-hidden="true">
      <i style={{ width: `${safeValue * 100}%` }} />
    </span>
  );
}

function MiniDonut({ value }: { value: number }) {
  const safeValue = Math.max(0, Math.min(1, value || 0));
  return <span className="mini-donut" style={{ "--value": `${safeValue * 100}%` } as CSSProperties} aria-hidden="true" />;
}

function routeFromWorkItem(item: DashboardWorkItem): RouteKey {
  if (item.item_type === "bid_pending_result") return "bids";
  if (item.item_type === "result_review") return "results";
  return "opportunities";
}

function statusFromDecision(status: DashboardOpportunityItem["decision_status"]): DashboardStatus {
  if (status === "planned") return "watch";
  if (status === "reviewing") return "info";
  if (status === "submitted") return "healthy";
  return "critical";
}

function statusFromBid(status: DashboardBidItem["status"]): DashboardStatus {
  if (status === "accepted") return "healthy";
  if (status === "rejected") return "critical";
  if (status === "reviewed") return "info";
  return "watch";
}

function statusFromOutcome(status: DashboardResultItem["award_outcome"]): DashboardStatus {
  if (status === "won") return "healthy";
  if (status === "lost") return "critical";
  return "info";
}

function statusFromSettlement(status: string): DashboardStatus {
  if (status === "settled") return "healthy";
  if (status === "ready_to_settle") return "watch";
  if (status === "waiting_result") return "watch";
  if (status === "before_deadline") return "info";
  if (status === "deadline_missing") return "critical";
  return "info";
}

function labelDecisionStatus(status: string): string {
  const labels: Record<string, string> = {
    planned: "예정",
    reviewing: "검토",
    submitted: "제출",
    skipped: "보류"
  };
  return labels[status] ?? status;
}

function labelBidStatus(status: string): string {
  const labels: Record<string, string> = {
    submitted: "제출",
    reviewed: "검토됨",
    accepted: "낙찰",
    rejected: "탈락"
  };
  return labels[status] ?? status;
}

function labelOutcome(status: string): string {
  const labels: Record<string, string> = {
    won: "낙찰",
    lost: "미낙찰",
    unknown: "확인"
  };
  return labels[status] ?? status;
}

function labelStatus(status: string): string {
  return labelDecisionStatus(status) || labelBidStatus(status) || status;
}

function formatMetricValue(metric: DashboardMetric): string {
  if (metric.value === null || metric.value === undefined) return "-";
  if (metric.unit === "ratio" && typeof metric.value === "number") return formatPercent(metric.value);
  return String(metric.value);
}

function formatCurrency(value?: number | null): string {
  if (value === null || value === undefined) return "-";
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency: "KRW",
    maximumFractionDigits: 0
  }).format(value);
}

function formatPercent(value?: number | null): string {
  if (value === null || value === undefined) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function numberFromSummary(value: number | string | null | undefined): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatSettlementMilestone(overview: PaperBiddingSettlementOverview): string {
  if (overview.status === "settled" && overview.latest_settled_at) {
    return `최종 정산 ${formatDateTime(overview.latest_settled_at)}`;
  }
  if (overview.status === "ready_to_settle") {
    return overview.next_confirmable_at ? `결과 입수 ${formatDateTime(overview.next_confirmable_at)}` : "결과 입수됨";
  }
  if (overview.status === "waiting_result") {
    return overview.oldest_waiting_deadline_at
      ? `마감 지남 ${formatDateTime(overview.oldest_waiting_deadline_at)}`
      : "마감 후 결과 대기";
  }
  if (overview.status === "before_deadline") {
    return overview.next_deadline_at ? `다음 마감 ${formatDateTime(overview.next_deadline_at)}` : "마감 전";
  }
  return "최종 결과 입수 시 확정";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", weekday: "short" }).format(new Date(value));
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatHours(value?: number | null): string {
  if (value === null || value === undefined) return "마감 미정";
  if (value < 0) return "마감 지남";
  if (value < 24) return `${value}시간`;
  return `${Math.floor(value / 24)}일`;
}

export default App;
