export interface GuideLink {
  label: string;
  path: string;
}

export interface GuideStep {
  no: number;
  title: string;
  description: string;
  caveat?: string;
  links: GuideLink[];
}

/** 탭 B — 이 앱 사용 흐름 (기존 8단계 워크플로) */
export const GUIDE_STEPS: GuideStep[] = [
  {
    no: 1,
    title: "전략 설정",
    description:
      "운영자 전략과 임계값을 먼저 설정합니다. 검토(review) 기준과 즉시투찰(bid_now) 기준을 정해 두면 이후 단계의 추천·결정이 이 기준을 따릅니다.",
    links: [{ label: "전략 편집 열기", path: "/dashboard/strategy" }],
  },
  {
    no: 2,
    title: "공고 수집 · 발굴",
    description:
      "나라장터(KONEPS) 공고를 수집한 뒤 입찰(=입찰 후보 발굴) 화면에서 추진할 만한 후보를 확인합니다. 수집 상태와 주기는 운영 · 수집 화면에서 점검합니다.",
    links: [
      { label: "운영 · 수집 열기", path: "/dashboard/operations" },
      { label: "입찰 후보 열기", path: "/dashboard/opportunities" },
    ],
  },
  {
    no: 3,
    title: "공고 분석",
    description:
      "관심 공고를 공고 탐색에서 자세히 봅니다. 분류 결과와 pgvector 기반 유사 공고로 경쟁 강도와 참고가(과거 유사 건 가격대)를 파악합니다.",
    links: [{ label: "공고 탐색 열기", path: "/dashboard/projects" }],
  },
  {
    no: 4,
    title: "가격 예측 & guardrail",
    description:
      "공고 상세에서 적정 투찰가 예측을 확인합니다. 카테고리 낙찰하한 미만의 추천은 guardrail이 자동으로 차단하므로, 하한 미만 가격은 추천·결정에 노출되지 않습니다.",
    links: [
      { label: "공고 상세 열기", path: "/dashboard/projects" },
      { label: "결정 게이트웨이 열기", path: "/dashboard/decisions" },
    ],
  },
  {
    no: 5,
    title: "투찰 결정",
    description:
      "결정 게이트웨이에서 review / bid_now 게이트로 각 건의 추진 여부를 판단합니다. 전략 임계값을 넘은 건만 즉시투찰 후보로 분류됩니다.",
    links: [{ label: "결정 게이트웨이 열기", path: "/dashboard/decisions" }],
  },
  {
    no: 6,
    title: "투찰 제출",
    description:
      "추진하기로 한 건의 투찰가를 확정하고 제출합니다. 투찰(=가격 제출) 화면에서 제출 상태를 관리합니다.",
    links: [{ label: "투찰 열기", path: "/dashboard/bids" }],
  },
  {
    no: 7,
    title: "결과 · 낙찰 확인",
    description:
      "결과 화면에서 낙찰 여부와 예측 오차(예측가 대비 실제 결과)를 확인합니다.",
    caveat:
      "표시되는 낙찰률/win rate는 가격 기준 추정치일 수 있습니다(실제 개찰 결과가 아니라 가격 비교 기반 추정). 해석 시 참고만 하세요.",
    links: [{ label: "결과 열기", path: "/dashboard/results" }],
  },
  {
    no: 8,
    title: "피드백 · 전략 튜닝",
    description:
      "결과를 바탕으로 전략 임계값을 보정하고, 합성 백테스트로 변경 효과를 검증한 뒤 다음 사이클에 반영합니다.",
    caveat:
      "백테스트의 낙찰률 또한 가격 기준 추정 프록시이므로, 절대치보다 전후 비교 추세로 보는 것이 안전합니다.",
    links: [
      { label: "전략 편집 열기", path: "/dashboard/strategy" },
      { label: "합성 백테스트 열기", path: "/dashboard/synthetic-backtest" },
    ],
  },
];

/** 탭 A — 나라장터 입찰 절차 플로우 단계 */
export interface KonepsProcessStep {
  no: number;
  title: string;
  summary: string;
  /** 이 서비스가 돕는 내용. external-only 단계는 null */
  ourHelp: string | null;
  /** 나라장터에서 직접 진행하는 단계인지 */
  external: boolean;
  links: GuideLink[];
}

export const KONEPS_PROCESS_STEPS: KonepsProcessStep[] = [
  {
    no: 1,
    title: "가입·인증",
    summary:
      "나라장터(g2b) 사업자 회원가입, 지문보안토큰(공동인증서) 등록, 입찰대리인 지정을 마칩니다.",
    ourHelp: null,
    external: true,
    links: [],
  },
  {
    no: 2,
    title: "입찰참가자격 등록",
    summary:
      "업종·면허(건설업/정보통신/엔지니어링 등), 시공능력평가액·실적, 수행지역을 나라장터에 신고합니다.",
    ourHelp:
      "회사 프로필에 면허·지역·시공능력평가액·도급한도·실적을 입력하면 공고별 참가 자격 충족 여부를 자동 매칭·경고합니다.",
    external: true,
    links: [{ label: "회사 정보 편집", path: "/dashboard/profile" }],
  },
  {
    no: 3,
    title: "공고 발굴",
    summary: "나라장터에 입찰공고가 게시됩니다.",
    ourHelp:
      "공고를 자동 수집·분류하고, 자격 충족 + 선호/제외 필터로 추진할 만한 후보를 발굴합니다.",
    external: false,
    links: [
      { label: "입찰 후보", path: "/dashboard/opportunities" },
      { label: "운영·수집", path: "/dashboard/operations" },
    ],
  },
  {
    no: 4,
    title: "공고 분석",
    summary: "경쟁 강도와 적정 가격대를 파악합니다.",
    ourHelp:
      "pgvector 유사 공고로 경쟁 강도와 참고가(과거 유사 건 가격대)를 보고, 공동도급·지역의무·유사실적·도급한도 초과 같은 리스크 신호를 표시합니다.",
    external: false,
    links: [{ label: "공고 탐색", path: "/dashboard/projects" }],
  },
  {
    no: 5,
    title: "투찰가 결정·제출",
    summary: "나라장터 전자입찰에서 투찰가를 제출합니다.",
    ourHelp:
      "적정 투찰가를 예측하고 카테고리 낙찰하한 미만 추천은 guardrail이 자동 차단합니다. 결정 게이트(review/bid_now)로 추진 여부를 판단합니다. 실제 투찰 제출은 나라장터에서 운영자가 직접 합니다.",
    external: false,
    links: [
      { label: "결정 게이트웨이", path: "/dashboard/decisions" },
      { label: "투찰", path: "/dashboard/bids" },
    ],
  },
  {
    no: 6,
    title: "개찰·낙찰",
    summary:
      "예정가격(복수예비가격 추첨 기반) 산정 후 적격심사/최저가/종합심사로 낙찰자를 결정합니다.",
    ourHelp:
      "개찰 결과를 수집·정산하여 예측가 대비 실제 오차와 낙찰 여부를 추적하고, 합성 백테스트로 전략을 보정합니다.",
    external: false,
    links: [
      { label: "결과", path: "/dashboard/results" },
      { label: "합성 백테스트", path: "/dashboard/synthetic-backtest" },
    ],
  },
];

/** 탭 A — 카테고리별 핵심 요약 */
export interface KonepsCategory {
  key: "construction" | "service" | "goods";
  title: string;
  requirements: string[];
  awardMethods: string[];
  ourMapping: string;
}

export const KONEPS_CATEGORIES: KonepsCategory[] = [
  {
    key: "construction",
    title: "공사",
    requirements: [
      "해당 업종 건설업 면허",
      "시공능력평가액·도급한도(추정가격 대비)",
      "지역제한·지역의무공동도급",
      "동일·유사 시공 실적",
    ],
    awardMethods: [
      "적격심사(추정가격 구간별 심사기준)",
      "종합심사낙찰제(대형공사)",
    ],
    ourMapping:
      "시공능력평가액·도급한도·지역·면허를 프로필과 매칭하고, 도급한도 초과 시 리스크로 경고합니다.",
  },
  {
    key: "service",
    title: "용역",
    requirements: [
      "해당 용역 업종 등록(엔지니어링/정보통신/일반용역 등)",
      "수행 실적·기술자 보유",
      "협상에 의한 계약은 제안서(기술) 평가 포함",
    ],
    awardMethods: [
      "적격심사",
      "협상에 의한 계약(기술+가격 평가)",
      "종합심사",
    ],
    ourMapping:
      "업종·실적·키워드로 적합 공고를 매칭하고 경쟁 강도·참고가를 제공합니다.",
  },
  {
    key: "goods",
    title: "물품",
    requirements: [
      "제조/구매 구분, 직접생산확인",
      "조달 등록(다수공급자계약 MAS 포함)",
      "규격·납품 조건 적합",
    ],
    awardMethods: ["적격심사", "최저가", "2단계 경쟁입찰"],
    ourMapping: "업종·키워드로 적합 공고를 매칭하고 참고가를 제공합니다.",
  },
];
