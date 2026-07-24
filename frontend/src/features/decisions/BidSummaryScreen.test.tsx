import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { BidReportEmailDeliveryResponse } from "@/shared/api";
import type { DashboardSummaryResponse } from "@/shared/types";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";
import type { BidFormDraftResponse } from "@/shared/types/bidFormDraft";

const emptySummary: DashboardSummaryResponse = {
  operator_id: 1,
  generated_at: "2026-05-19T00:00:00Z",
  today: "2026-05-19",
  operational_status: {
    key: "operator_strategy",
    label: "운영 상태",
    value: "completed",
    unit: "state",
    status: "healthy",
    detail: "정상"
  },
  metrics: [],
  work_items: [],
  sections: [
    { key: "opportunities", label: "입찰", count: 0, status: "healthy", href: "/dashboard/opportunities" },
    { key: "bids", label: "투찰", count: 0, status: "healthy", href: "/dashboard/bids" },
    { key: "results", label: "결과", count: 0, status: "healthy", href: "/dashboard/results" }
  ],
  recent_opportunities: [],
  recent_bids: [],
  recent_results: [],
  realtime_href: "/api/v1/realtime/events"
};

const bidSummary: BidSummaryResponse = {
  decision_record_id: 101,
  operator_id: 1,
  generated_at: "2026-06-12T00:00:00Z",
  notice: {
    project_id: 11,
    title: "테스트 공항 시설 공고",
    notice_number: "R26BK00123-001",
    category: "construction",
    business_type_label: "토목공사",
    budget_estimate: 500_000_000,
    demand_agency: "한국공항공사",
    issuing_agency: "조달청",
    deadline: "2026-06-20T05:00:00Z",
    source_url: null,
    status: "open"
  },
  recommendation: {
    recommended_amount: 430_000_000,
    recommended_bid_rate: 0.86,
    probability_score: 0.72,
    action: "bid_now",
    decision_status: "planned",
    priority_score: 0.8,
    matched_score: 0.75,
    competitiveness_score: 0.65,
    reasoning: "면허·지역·예산 모두 충족하며 예측 적정대 내 가격입니다.",
    strengths: ["면허 일치", "지역 우대"],
    risk_flags: ["경쟁 다수"]
  },
  prediction: {
    predicted_price: 432_000_000,
    predicted_bid_rate: 0.864,
    price_range_min: 420_000_000,
    price_range_max: 445_000_000,
    confidence_score: 0.7,
    pricing_mode: "model",
    predictor_name: "price-predictor-v3",
    guardrail_applied: true,
    floor_bid_rate: 0.8,
    created_at: "2026-06-11T00:00:00Z"
  },
  category_floor: {
    category: "construction",
    business_group: "토목",
    floor_bid_rate: 0.8,
    floor_price: 400_000_000,
    note: "카테고리 낙찰하한율(참고)입니다. 실제 낙찰하한가는 개찰 시 결정됩니다."
  },
  field_stat: {
    category: "construction",
    settled_count: 42,
    est_price_close_rate: 0.31,
    eligible_favorable_rate: 0.58,
    source_run_id: 7,
    source_operator_slug: "synthetic-aggressive",
    note: "최신 백테스트 분야 추정 지표입니다. 실제 낙찰률이 아닙니다."
  },
  direct_submission_notice:
    "이 요약은 투찰 판단 참고용입니다. 실제 나라장터(KONEPS) 투찰서 작성·제출은 운영자가 직접 진행해야 합니다."
};

const bidFormDraft: BidFormDraftResponse = {
  decision_record_id: 101,
  operator_id: 1,
  generated_at: "2026-06-12T00:00:00Z",
  notice_number: "R26BK00123-001",
  title: "테스트 공항 시설 공고",
  demand_agency: "한국공항공사",
  budget_estimate: 500_000_000,
  recommended_amount: 430_000_000,
  recommended_bid_rate: 0.86,
  category: "construction",
  business_type_label: "토목공사",
  deadline: "2026-06-20T05:00:00Z",
  eligibility_estimate: "적격 추정",
  eligibility_note:
    "카테고리 낙찰하한율(참고) 대비 추천 투찰가 위치에서 도출한 추정 라벨입니다.",
  lottery_numbers: [3, 11],
  lottery_numbers_note:
    "무작위 편의 픽입니다. 개별 번호 선택은 낙찰 결과에 영향이 없습니다.",
  fields: [
    {
      key: "recommended_amount",
      field_label: "투찰금액",
      value: "₩430,000,000",
      raw_value: 430_000_000,
      note: null
    },
    {
      key: "recommended_bid_rate",
      field_label: "투찰률",
      value: "86.0%",
      raw_value: 0.86,
      note: "추천 투찰가 / 추정가격 기준입니다."
    },
    {
      key: "lottery_numbers",
      field_label: "복수예비가격 추첨번호(무작위 2개)",
      value: "3, 11",
      raw_value: null,
      note: "무작위 편의 픽입니다. 개별 번호 선택은 낙찰 결과에 영향이 없습니다."
    }
  ],
  direct_submission_notice:
    "이 투찰서 초안은 참고용입니다. 실제 나라장터(KONEPS) 투찰서 작성·제출은 운영자가 직접 진행해야 합니다."
};

const dryRunEmail: BidReportEmailDeliveryResponse = {
  project_id: 11,
  decision_record_id: 101,
  dry_run: true,
  delivery_status: "dry_run_rendered",
  masked_recipient: "op***@example.com",
  subject: "[투찰 요약] 테스트 공항 시설 공고",
  has_draft_attachment: true,
  notice:
    "이 요약은 투찰 판단 참고용입니다. 실제 나라장터(KONEPS) 투찰서 작성·제출은 운영자가 직접 진행해야 하며, 추천 투찰가는 보장된 낙찰가가 아닙니다."
};

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    text: () => Promise.resolve(JSON.stringify(payload)),
    headers: { get: () => null }
  } as unknown as Response);
}

function textResponse(body: string, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve(body),
    headers: { get: () => null }
  } as unknown as Response);
}

/**
 * fetch mock that serves the summary/draft screen and delegates the
 * report-email POST to `reportEmail`. Mirrors the inline mocks above but keeps
 * the many email-toast cases from copy-pasting the same 3 branches.
 */
function emailReportFetch(reportEmail: () => Promise<Response>) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
    if (url.endsWith("/api/v1/operations/bid-decisions/101/summary")) {
      return jsonResponse(bidSummary);
    }
    if (url.includes("/bid-decisions/101/bid-form-draft?format=json")) {
      return jsonResponse(bidFormDraft);
    }
    if (
      url.endsWith("/api/v1/operations/bid-decisions/101/report-email") &&
      init?.method === "POST"
    ) {
      return reportEmail();
    }
    return jsonResponse({}, 404);
  });
}

/** Wait for the 메일로 보내기 button to enable, then click it. */
async function clickSendEmail() {
  const emailBtn = await screen.findByRole("button", { name: "메일로 보내기" });
  await waitFor(() => expect(emailBtn).not.toBeDisabled());
  fireEvent.click(emailBtn);
  return emailBtn;
}

beforeEach(() => {
  window.localStorage.setItem("bid-vector-dashboard-token", "token-summary");
  window.history.pushState({}, "", "/dashboard/decisions/101/summary");
  vi.restoreAllMocks();
  // Toast state is module-global and only auto-clears after 4s, so reset it
  // between tests to keep the honesty toast assertions isolated.
  toastApi.clearAll();
});

describe("BidSummaryScreen", () => {
  it("추천 투찰가·투찰률·가격 적합도(추정)·근거·하한율 참고·분야 통계·직접 제출 안내를 렌더한다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.endsWith("/api/v1/operations/bid-decisions/101/summary")) {
        return jsonResponse(bidSummary);
      }
      if (url.includes("/bid-decisions/101/bid-form-draft?format=json")) {
        return jsonResponse(bidFormDraft);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "투찰 의사결정 요약", level: 2 })
    ).toBeInTheDocument();

    // 추천 투찰가/투찰률 (요약 + 초안 두 곳에 나타날 수 있음)
    expect(await screen.findByText("추천 투찰가")).toBeInTheDocument();
    expect(screen.getAllByText("₩430,000,000").length).toBeGreaterThan(0);
    expect(screen.getAllByText("86.0%").length).toBeGreaterThan(0);

    // 가격 적합도(추정) 라벨 정직성 — "낙찰 확률/가능성" 류 문구가 없어야 함.
    expect(screen.getAllByText("가격 적합도(추정)").length).toBeGreaterThan(0);
    expect(screen.getByText("72.0%")).toBeInTheDocument();
    expect(screen.queryByText(/낙찰 확률/)).toBeNull();
    expect(screen.queryByText(/낙찰 가능성/)).toBeNull();

    // 근거 / 강점 / 리스크
    expect(
      screen.getByText("면허·지역·예산 모두 충족하며 예측 적정대 내 가격입니다.")
    ).toBeInTheDocument();
    expect(screen.getByText("면허 일치")).toBeInTheDocument();
    expect(screen.getByText("경쟁 다수")).toBeInTheDocument();

    // 카테고리 낙찰하한율(참고) + 개찰 전 미공개 note
    expect(screen.getByText("카테고리 낙찰하한율 (참고)")).toBeInTheDocument();
    expect(
      screen.getByText(/개찰 전에는 실제 낙찰하한가가 미공개/)
    ).toBeInTheDocument();

    // 복수예비가격 추첨번호 정직성 — 라벨에 "무작위", note 노출, "추천/최적번호" 금지.
    expect(
      await screen.findByText("복수예비가격 추첨번호(무작위 2개)")
    ).toBeInTheDocument();
    expect(screen.getByText(/무작위 편의 픽/)).toBeInTheDocument();
    expect(screen.queryByText(/추천번호|최적번호/)).toBeNull();

    // 분야 통계
    expect(screen.getByText("분야 통계 (백테스트 추정)")).toBeInTheDocument();
    expect(screen.getByText("42건")).toBeInTheDocument();

    // 직접 제출 안내가 눈에 띄게(서버 문구 그대로) — 요약 + 초안 두 곳
    expect(
      screen.getAllByText(/실제 나라장터\(KONEPS\) 투찰서 작성·제출은 운영자가 직접 진행/)
        .length
    ).toBeGreaterThan(0);

    // 투찰서 초안 — 나라장터 입력 항목 매핑표
    expect(
      await screen.findByRole("heading", { name: "투찰서 초안 (나라장터 입력 항목)" })
    ).toBeInTheDocument();
    expect(screen.getByText("투찰금액")).toBeInTheDocument();
    expect(screen.getAllByText("₩430,000,000").length).toBeGreaterThan(0);
    expect(screen.getByText("적격여부(추정)")).toBeInTheDocument();
    expect(screen.getByText("적격 추정")).toBeInTheDocument();
    // 자동 제출 아님 — 초안의 정직 안내 문구
    expect(
      screen.getByText(/이 투찰서 초안은 참고용입니다/)
    ).toBeInTheDocument();

    // 액션 버튼
    expect(screen.getByRole("button", { name: "CSV 다운로드" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "초안 복사" })).toBeInTheDocument();
  });

  it("CSV 다운로드 버튼은 백엔드 CSV 렌더를 Blob 으로 받아 임시 anchor 로 내려받는다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.endsWith("/api/v1/operations/bid-decisions/101/summary")) {
        return jsonResponse(bidSummary);
      }
      if (url.includes("/bid-decisions/101/bid-form-draft?format=json")) {
        return jsonResponse(bidFormDraft);
      }
      if (url.includes("/bid-decisions/101/bid-form-draft?format=csv")) {
        return textResponse("field_label,value\r\n투찰금액,₩430000000\r\n");
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    // jsdom 의 URL 에는 createObjectURL/revokeObjectURL 이 없으므로 메서드만 주입.
    // (전체 URL 객체를 교체하면 react-router 의 new URL() 이 깨짐.)
    const origCreate = (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
    const origRevoke = (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = revokeObjectURL;

    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    renderApp();

    const downloadBtn = await screen.findByRole("button", { name: "CSV 다운로드" });
    // 초안 JSON 이 로드돼 버튼이 활성화될 때까지 대기.
    await waitFor(() => expect(downloadBtn).not.toBeDisabled());
    fireEvent.click(downloadBtn);

    await waitFor(() => {
      expect(createObjectURL).toHaveBeenCalledTimes(1);
    });
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock");
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("bid-form-draft?format=csv")
      )
    ).toBe(true);

    (URL as unknown as { createObjectURL: unknown }).createObjectURL = origCreate;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = origRevoke;
  });

  it("초안 복사 버튼은 백엔드 text 렌더를 클립보드에 기록한다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.endsWith("/api/v1/operations/bid-decisions/101/summary")) {
        return jsonResponse(bidSummary);
      }
      if (url.includes("/bid-decisions/101/bid-form-draft?format=json")) {
        return jsonResponse(bidFormDraft);
      }
      if (url.includes("/bid-decisions/101/bid-form-draft?format=text")) {
        return textResponse("[투찰서 초안]\n투찰금액: ₩430,000,000\n");
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });

    renderApp();

    const copyBtn = await screen.findByRole("button", { name: "초안 복사" });
    await waitFor(() => expect(copyBtn).not.toBeDisabled());
    fireEvent.click(copyBtn);

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining("투찰금액: ₩430,000,000")
    );
  });

  it("메일로 보내기 버튼은 report-email 엔드포인트를 POST 호출하고 dry-run 결과를 정직하게(미발송) 토스트한다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.endsWith("/api/v1/operations/bid-decisions/101/summary")) {
        return jsonResponse(bidSummary);
      }
      if (url.includes("/bid-decisions/101/bid-form-draft?format=json")) {
        return jsonResponse(bidFormDraft);
      }
      if (
        url.endsWith("/api/v1/operations/bid-decisions/101/report-email") &&
        init?.method === "POST"
      ) {
        return jsonResponse(dryRunEmail);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    const emailBtn = await screen.findByRole("button", { name: "메일로 보내기" });
    // 요약이 로드돼 버튼이 활성화될 때까지 대기.
    await waitFor(() => expect(emailBtn).not.toBeDisabled());
    fireEvent.click(emailBtn);

    // 올바른 decisionRecordId(101) 로 POST 호출.
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith(
              "/api/v1/operations/bid-decisions/101/report-email"
            ) && (init as RequestInit | undefined)?.method === "POST"
        )
      ).toBe(true);
    });

    // dry-run 토스트 — 미발송을 명시하고 마스킹 수신자/제목을 노출.
    expect(
      await screen.findByText("메일 미리보기 생성됨(dry-run)")
    ).toBeInTheDocument();
    expect(screen.getByText(/실제 메일은 발송되지 않았습니다/)).toBeInTheDocument();
    expect(screen.getByText(/op\*\*\*@example\.com/)).toBeInTheDocument();
    // 정직 §2 — dry-run 을 실제 송신으로 표시하지 않는다.
    expect(screen.queryByText("메일을 보냈습니다.")).toBeNull();
  });

  it("delivery_status=sent 이라도 dry_run=true 이면 미발송(미리보기) 토스트로 강등한다 (정직 §2)", async () => {
    // 백엔드 계약상 일어나선 안 되는 조합이지만, resolveEmailStatusKey 의
    // 방어적 강등을 핀으로 고정한다 — sent 문구가 절대 새어나오면 안 된다.
    const sentButDryRun: BidReportEmailDeliveryResponse = {
      ...dryRunEmail,
      delivery_status: "sent",
      dry_run: true
    };
    vi.stubGlobal("fetch", emailReportFetch(() => jsonResponse(sentButDryRun)));

    renderApp();
    await clickSendEmail();

    // 경고(미리보기) 토스트 — warning 은 role="alert" 로 렌더된다.
    const toastTitle = await screen.findByText("메일 미리보기 생성됨(dry-run)");
    expect(toastTitle.closest('[role="alert"]')).not.toBeNull();
    expect(screen.getByText(/실제 메일은 발송되지 않았습니다/)).toBeInTheDocument();
    expect(screen.queryByText("메일을 보냈습니다.")).toBeNull();
  });

  it("delivery_status=sent 이고 dry_run=false 이면 '메일을 보냈습니다' 성공 토스트를 표시한다", async () => {
    const sent: BidReportEmailDeliveryResponse = {
      ...dryRunEmail,
      delivery_status: "sent",
      dry_run: false
    };
    vi.stubGlobal("fetch", emailReportFetch(() => jsonResponse(sent)));

    renderApp();
    await clickSendEmail();

    // 성공 토스트 — success 는 role="status" 로 렌더된다.
    const toastTitle = await screen.findByText("메일을 보냈습니다.");
    expect(toastTitle.closest('[role="status"]')).not.toBeNull();
    // 라이브 송신이므로 미리보기 문구는 없어야 한다.
    expect(screen.queryByText("메일 미리보기 생성됨(dry-run)")).toBeNull();
  });

  it("skipped_no_recipient 응답은 경고 토스트로 표시하고 성공 문구를 쓰지 않는다", async () => {
    const skipped: BidReportEmailDeliveryResponse = {
      ...dryRunEmail,
      delivery_status: "skipped_no_recipient",
      masked_recipient: ""
    };
    vi.stubGlobal("fetch", emailReportFetch(() => jsonResponse(skipped)));

    renderApp();
    await clickSendEmail();

    const toastTitle = await screen.findByText(
      "수신자 이메일이 없어 메일 전달을 건너뛰었습니다."
    );
    expect(toastTitle.closest('[role="alert"]')).not.toBeNull();
    expect(screen.queryByText("메일을 보냈습니다.")).toBeNull();
  });

  it("failed 응답은 위험 토스트로 표시하고 성공 문구를 쓰지 않는다", async () => {
    const failed: BidReportEmailDeliveryResponse = {
      ...dryRunEmail,
      delivery_status: "failed",
      dry_run: false
    };
    vi.stubGlobal("fetch", emailReportFetch(() => jsonResponse(failed)));

    renderApp();
    await clickSendEmail();

    const toastTitle = await screen.findByText("메일 전달에 실패했습니다.");
    expect(toastTitle.closest('[role="alert"]')).not.toBeNull();
    expect(screen.queryByText("메일을 보냈습니다.")).toBeNull();
  });

  it("엔드포인트가 실패(500)하면 위험(danger) 에러 토스트를 표시한다", async () => {
    vi.stubGlobal(
      "fetch",
      emailReportFetch(() => jsonResponse({ detail: "boom" }, 500))
    );

    renderApp();
    await clickSendEmail();

    const toastTitle = await screen.findByText("메일 전달 요청 실패");
    expect(toastTitle.closest('[role="alert"]')).not.toBeNull();
    expect(screen.queryByText("메일을 보냈습니다.")).toBeNull();
  });

  it("로딩 중에는 로딩 문구를, 404 응답에는 에러 alert을 렌더한다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      // 요약 엔드포인트는 404 (없는 결정 기록)
      return jsonResponse({ detail: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "투찰 의사결정 요약", level: 2 })
    ).toBeInTheDocument();

    // 404 → 에러 alert
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "투찰 요약을 불러오지 못했습니다."
      );
    });

    // 요약 데이터가 없으면 메일로 보내기 버튼은 비활성(형제 액션과 동일).
    expect(screen.getByRole("button", { name: "메일로 보내기" })).toBeDisabled();
  });

  it("prediction·field_stat 가 null 이면 graceful empty 문구를 렌더한다", async () => {
    const minimal: BidSummaryResponse = {
      ...bidSummary,
      recommendation: {
        ...bidSummary.recommendation,
        recommended_bid_rate: null,
        strengths: [],
        risk_flags: []
      },
      prediction: null,
      field_stat: null,
      category_floor: { ...bidSummary.category_floor, floor_bid_rate: null, floor_price: null }
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.endsWith("/api/v1/operations/bid-decisions/101/summary")) {
        return jsonResponse(minimal);
      }
      if (url.includes("/bid-decisions/101/bid-form-draft?format=json")) {
        return jsonResponse(bidFormDraft);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "투찰 의사결정 요약", level: 2 })
    ).toBeInTheDocument();

    expect(
      await screen.findByText("이 결정에 연결된 가격 예측이 없습니다.")
    ).toBeInTheDocument();
    expect(
      screen.getByText("이 분야의 백테스트 통계가 아직 없습니다.")
    ).toBeInTheDocument();
    expect(
      screen.getByText("이 카테고리에 설정된 낙찰하한율이 없습니다.")
    ).toBeInTheDocument();
    expect(screen.getByText("표시할 강점이 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("감지된 주요 리스크가 없습니다.")).toBeInTheDocument();
  });
});
