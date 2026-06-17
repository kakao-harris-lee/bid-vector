import type { components } from "./openapi.d";

// 의사결정 증적 샘플 (GET /api/v1/operations/decision-samples).
// 생성된 OpenAPI 스키마에서 alias 해서 백엔드 계약과 자동 동기화한다 —
// openapi.d.ts 는 수기 편집 금지(sync-types 스킬로만 재생성).
export type DecisionSamplesResponse = components["schemas"]["DecisionSamplesResponse"];
export type DecisionSampleItem = components["schemas"]["DecisionSampleItem"];
export type DecisionSamplePrediction = components["schemas"]["DecisionSamplePrediction"];
