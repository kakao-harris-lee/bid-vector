import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import {
  formatCurrency,
  formatCurrencyCompact,
  formatDateTime,
  formatPercent
} from "@/shared/lib";
import type { BidDecisionRecordResponse } from "@/shared/types/project";
import { SimilarPanel } from "./SimilarPanel";
import { useProjectQuery, useTimelineQuery } from "./hooks";

const ACTION_LABEL: Record<BidDecisionRecordResponse["action"], string> = {
  bid_now: "투찰",
  review: "검토",
  skip: "보류"
};

const ACTION_TONE: Record<BidDecisionRecordResponse["action"], "healthy" | "watch" | "muted"> = {
  bid_now: "healthy",
  review: "watch",
  skip: "muted"
};

const DECISION_LABEL: Record<BidDecisionRecordResponse["decision_status"], string> = {
  planned: "예정",
  reviewing: "검토 중",
  submitted: "제출",
  skipped: "보류"
};

export function ProjectDetailScreen() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { session } = useShellContext();
  const projectId = id ? Number.parseInt(id, 10) : NaN;
  const idIsValid = Number.isFinite(projectId);

  const project = useProjectQuery(session, idIsValid ? projectId : null);
  const timeline = useTimelineQuery(session, idIsValid ? projectId : null, 10);

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
              <DetailRow label="예산" value={formatCurrency(project.data.budget_estimate)} />
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
                    <Badge tone="info">{DECISION_LABEL[record.decision_status]}</Badge>
                  </div>
                </div>
                <div className="flex items-center justify-between tabular-nums text-[var(--color-muted)]">
                  <span>우선순위 {formatPercent(record.priority_score)}</span>
                  <span>{formatCurrencyCompact(record.recommended_amount)}</span>
                </div>
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
    <div className="grid grid-cols-[80px_1fr] gap-2 text-sm">
      <dt className="text-[var(--color-muted)]">{label}</dt>
      <dd className="break-words text-[var(--color-fg)]">{value}</dd>
    </div>
  );
}
