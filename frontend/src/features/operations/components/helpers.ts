import type {
  G2EvidenceStatus,
  OperationsCardStatus,
  SmokeTestPhaseRate
} from "@/shared/types/operations";

export const TONE: Record<OperationsCardStatus, "info" | "healthy" | "watch" | "critical"> = {
  info: "info",
  healthy: "healthy",
  watch: "watch",
  critical: "critical"
};

// smoke 사이클 phase 이름 → 한국어 라벨. 모르는 이름은 원본 그대로 노출.
const SMOKE_PHASE_LABELS: Record<string, string> = {
  koneps_collect: "공고 수집",
  sbert_embedding: "임베딩",
  predict_price: "가격 예측",
  telegram_ping: "텔레그램 발신"
};

const SMOKE_FAILURE_CATEGORY_LABELS: Record<string, string> = {
  credential: "인증/키",
  koneps_response: "나라장터 응답",
  no_candidate: "후보 없음",
  telegram: "텔레그램",
  task_broker: "태스크/브로커",
  db_schema: "DB/schema",
  prediction: "예측",
  unknown: "미분류"
};

export function smokePhaseLabel(name: string): string {
  return SMOKE_PHASE_LABELS[name] ?? name;
}

export function smokeFailureCategoryLabel(name: string): string {
  return SMOKE_FAILURE_CATEGORY_LABELS[name] ?? name;
}

// 표본 상태 → 라벨. 미지/미보고 상태는 "미실행"로 폴백.
const SAMPLE_STATUS_LABELS: Record<string, string> = {
  sufficient: "충분",
  insufficient_sample: "표본 부족"
};

export function sampleStatusLabel(status?: string | null): string {
  return SAMPLE_STATUS_LABELS[status ?? ""] ?? "미실행";
}

// run 상태 → 배지 톤. 미지 상태는 muted 로 폴백.
const RUN_STATUS_TONES: Record<string, "healthy" | "watch" | "critical" | "info" | "muted"> = {
  completed: "healthy",
  failed: "critical",
  running: "watch",
  queued: "watch"
};

export function runStatusTone(status?: string | null): "healthy" | "watch" | "critical" | "info" | "muted" {
  return RUN_STATUS_TONES[status ?? ""] ?? "muted";
}

// phase 통과율 → 톤. 데이터가 없으면(평가 0건) muted로 정직하게 표기.
export function smokePhaseTone(rate: SmokeTestPhaseRate): "healthy" | "watch" | "critical" | "muted" {
  if (rate.evaluated_count === 0) return "muted";
  if (rate.pass_rate >= 0.9) return "healthy";
  if (rate.pass_rate >= 0.6) return "watch";
  return "critical";
}

// G-2 증적 상태 → 배지 톤. 미지 상태는 info(중립 고지)로 폴백.
const G2_STATUS_TONES: Record<string, "healthy" | "watch" | "critical" | "info" | "muted"> = {
  ready: "healthy",
  insufficient: "watch",
  mixed_scope: "critical",
  missing: "muted"
};

export function g2StatusTone(status?: string | null): "healthy" | "watch" | "critical" | "info" | "muted" {
  return G2_STATUS_TONES[status ?? ""] ?? "info";
}

// G-2 증적 상태 → 한국어 라벨. 미지 상태는 원문 그대로 노출(조용한 오표시 금지).
const G2_STATUS_LABELS: Record<G2EvidenceStatus, string> = {
  ready: "증적 충분",
  insufficient: "증적 부족",
  mixed_scope: "범위 혼합",
  missing: "증적 없음"
};

export function g2StatusLabel(status?: string | null): string {
  if (status && status in G2_STATUS_LABELS) return G2_STATUS_LABELS[status as G2EvidenceStatus];
  return status ?? "missing";
}
