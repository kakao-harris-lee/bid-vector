import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatDateTime, formatPercent } from "@/shared/lib";
import { AmountWithBasis } from "@/shared/components";
import { AMOUNT_BASIS_LABEL, BID_BASE_NOTE } from "@/shared/constants/amountBasis";
import {
  ACTION_LABEL,
  ACTION_TONE,
  DECISION_STATUS_LABEL
} from "@/shared/constants/decisionLabels";
import { DecisionReasonsCard } from "@/features/dashboard/components";
import { trackProjectView } from "@/shared/api";
import { SimilarPanel } from "./SimilarPanel";
import { useProjectQuery, useTimelineQuery } from "./hooks";

export function ProjectDetailScreen() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { session } = useShellContext();
  const projectId = id ? Number.parseInt(id, 10) : NaN;
  const idIsValid = Number.isFinite(projectId);

  const project = useProjectQuery(session, idIsValid ? projectId : null);
  const timeline = useTimelineQuery(session, idIsValid ? projectId : null, 10);

  // Roadmap C-1 (a): log a `project_view` analytics event whenever the
  // operator opens a tender detail. Backend keeps only the first view per
  // (operator, project) for review-time math, so re-fires are harmless.
  // Best-effort: telemetry failure never affects render.
  useEffect(() => {
    if (!idIsValid) return;
    const token = session?.token;
    if (!token) return;
    void trackProjectView(projectId, token);
  }, [idIsValid, projectId, session?.token]);

  if (!idIsValid) {
    return (
      <p className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]">
        잘못된 공고 ID 입니다.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
      <section className="flex flex-col gap-4">
        <header className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => navigate("/dashboard/projects")}
            aria-label="목록으로"
          >
            <ArrowLeft size={14} className="mr-1" />
            목록
          </Button>
          <h2 className="text-lg font-semibold text-[var(--color-fg)]">공고 상세</h2>
        </header>

        {project.isPending && !project.data ? (
          <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
        {project.error ? (
          <p className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]" role="alert">
            {project.error.message ?? "공고 상세를 불러오지 못했습니다."}
          </p>
        ) : null}

        {project.data ? (
          <Card>
            <CardHeader>
              <CardTitle>{project.data.title}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm">
              <DetailRow label="카테고리" value={project.data.category} />
              <DetailRow label="공고번호" value={project.data.notice_number ?? "-"} />
              <DetailRow label="발주기관" value={project.data.issuing_agency ?? "-"} />
              <DetailRow label="수요기관" value={project.data.demand_agency ?? "-"} />
              {/* 추정가격과 투찰 기준금액을 한 이름("예산")으로 묶지 않는다 — 투찰율은
                  기초금액에 곱해지고 두 금액은 과세 공고에서 어긋난다. */}
              <DetailRow
                label={AMOUNT_BASIS_LABEL.estimate}
                value={
                  <AmountWithBasis
                    amount={project.data.budget_estimate}
                    basis="estimate"
                    variant="inline"
                  />
                }
              />
              {project.data.bid_base_amount ? (
                <DetailRow
                  label={AMOUNT_BASIS_LABEL.bid_base}
                  value={
                    <AmountWithBasis
                      amount={project.data.bid_base_amount}
                      basis="bid_base"
                      source={project.data.bid_base_source}
                      ratio={project.data.bid_base_to_estimate_ratio}
                      note={BID_BASE_NOTE}
                      label={null}
                    />
                  }
                />
              ) : null}
              <DetailRow label="상태" value={project.data.status} />
              <DetailRow label="등록" value={formatDateTime(project.data.created_at)} />
              {project.data.source_url ? (
                <DetailRow
                  label="원문"
                  value={
                    <a
                      href={project.data.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[var(--color-primary)] underline"
                    >
                      외부 링크 열기
                    </a>
                  }
                />
              ) : null}
              <DetailRow label="설명" value={project.data.description || "-"} />
              <DetailRow label="요구사항" value={project.data.requirements || "-"} />
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>
              최근 결정 타임라인
              {timeline.data ? (
                <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
                  ({timeline.data.result_count}건)
                </span>
              ) : null}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 text-xs">
            {timeline.error ? (
              <p className="text-[var(--color-danger)]" role="alert">
                {timeline.error.message ?? "타임라인을 불러오지 못했습니다."}
              </p>
            ) : null}
            {timeline.isPending && !timeline.data ? (
              <p className="text-[var(--color-muted)]">불러오는 중…</p>
            ) : null}
            {timeline.data && timeline.data.timeline.length === 0 ? (
              <p className="text-[var(--color-muted)]">아직 기록된 입찰 결정이 없습니다.</p>
            ) : null}
            {timeline.data?.timeline.map((record) => (
              <article
                key={record.id}
                className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
              >
                <div className="flex items-center justify-between">
                  <span className="text-[var(--color-muted)]">{formatDateTime(record.updated_at)}</span>
                  <div className="flex items-center gap-1">
                    <Badge tone={ACTION_TONE[record.action]}>{ACTION_LABEL[record.action]}</Badge>
                    <Badge tone="info">{DECISION_STATUS_LABEL[record.decision_status]}</Badge>
                  </div>
                </div>
                <div className="flex items-center justify-between tabular-nums text-[var(--color-muted)]">
                  <span>우선순위 {formatPercent(record.priority_score)}</span>
                  <AmountWithBasis
                    amount={record.recommended_amount}
                    basis="submission"
                    variant="inline"
                    compact
                  />
                </div>
                {(record.strengths?.length ?? 0) > 0 || (record.risk_flags?.length ?? 0) > 0 ? (
                  <DecisionReasonsCard
                    compact
                    strengths={record.strengths}
                    riskFlags={record.risk_flags}
                    action={record.action}
                    priorityScore={record.priority_score}
                    probabilityScore={record.probability_score}
                    decisionRecordId={record.id}
                    projectId={projectId}
                    authToken={session?.token ?? null}
                  />
                ) : null}
                {record.reasoning ? (
                  <p className="text-[var(--color-fg)]">{record.reasoning}</p>
                ) : null}
              </article>
            ))}
          </CardContent>
        </Card>
      </section>

      <aside>
        <SimilarPanel projectId={projectId} />
      </aside>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[132px_1fr] gap-2 text-sm">
      <dt className="text-[var(--color-muted)]">{label}</dt>
      <dd className="break-words text-[var(--color-fg)]">{value}</dd>
    </div>
  );
}
