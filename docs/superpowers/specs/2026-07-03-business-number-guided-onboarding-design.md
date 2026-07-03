# 사업자번호 기반 반자동 온보딩 설계

작성일: 2026-07-03

## 배경

현재 `bid-vector`는 사용자가 회사 프로필과 감시 전략을 직접 입력하면, 기존
`CompanyProfile`/`OperatorStrategy`와 공고 매칭 엔진을 사용해 추천 후보를 찾는다. 이미
면허, 지역, 예산 범위, 시공능력평가액, 도급한도, 관심/제외 키워드는 후보 탐색에 쓰이고
있지만, 사업자번호를 저장하거나 사업자번호로 프로필을 자동 보강하는 흐름은 없다.

국세청 사업자등록정보 API는 사업자등록번호 상태조회와 진위확인에 적합하다. 다만 이 API만으로
공고 탐색에 필요한 면허, 수행 지역, 시공능력, 도급한도, 관심 업종을 확정할 수는 없다. 따라서
제품 방향은 "사업자번호만 입력하면 자동 추천이 끝나는" 방식이 아니라, 확인 가능한 값과 추론
후보를 구분하고 사용자가 확정한 값만 추천 엔진에 반영하는 반자동 온보딩이다.

참고 외부 출처:

- 공공데이터포털 `국세청_사업자등록정보 진위확인 및 상태조회 서비스`
  (`https://www.data.go.kr/tcs/dss/selectApiDataDetailView.do?publicDataPk=15081808`)

## 목표

1. 사업자번호 입력만으로 사업자 상태와 기본 진위 여부를 먼저 확인한다.
2. 확인 결과와 별도 보강 후보를 사용해 회사 프로필/전략 입력 부담을 줄인다.
3. 자동 추론값과 사용자 확정값을 명확히 구분해 추천 책임과 감사 가능성을 유지한다.
4. 온보딩 직후 기존 후보 미리보기 API로 "이 사업자에게 맞는 현재 공고"를 보여준다.

## 비목표

- 자동 투찰 또는 나라장터 자동 제출은 하지 않는다.
- 사업자번호만으로 면허, 지역, 시공능력, 도급한도, 관심 키워드를 확정하지 않는다.
- G-2 exit 이전에 실제 외부 사업자 SaaS 멀티테넌트 전환을 시작하지 않는다.
- 민감한 사업자번호 원문을 운영 로그나 G-2 evidence에 그대로 남기지 않는다.

## 권장 접근

### 1. 사업자 확인 단계

사용자는 사업자등록번호를 입력한다. 시스템은 국세청 사업자등록정보 API를 통해 상태조회 또는
진위확인을 수행한다.

저장/표시 원칙:

- 정상/휴업/폐업 상태, 과세유형, 폐업일자 같은 상태 정보는 온보딩 판단에 사용한다.
- 진위확인은 사업자번호 외에 개업일자, 대표자명 등 사용자가 입력한 값을 대조하는 흐름으로 둔다.
- 사업자번호는 암호화 저장 또는 masked display만 허용한다.
- API 실패, 미조회, 신규 개업 지연은 차단이 아니라 "확인 필요" 상태로 둔다.

### 2. 프로필 후보 생성 단계

자동 확정이 아니라 후보를 생성한다. 후보 소스는 단계적으로 붙인다.

- 기본 후보: 사용자가 선택한 업종, 지역, 면허 chip, 예산 범위.
- 국세청 API 후보: 사업자 상태와 진위 확인 결과.
- KONEPS/내부 공고 후보: 기존 공고의 업무구분, 면허제한, 지역제한, 금액대에서 역으로 추천 가능한
  `business_type`, `license_codes`, `region_codes`, `min_budget_estimate`, `max_budget_estimate`.
- 향후 외부 보강 후보: 건설/전문건설/엔지니어링 등 업종별 공적 조회 출처가 확인된 경우에만
  시공능력평가액, 면허, 영업지역 후보로 추가한다.

후보에는 항상 `source`, `confidence`, `needs_confirmation`, `reason`을 붙인다.

### 3. 사용자 확정 단계

사용자는 자동입력 후보를 검토하고 확정한다. 확정된 값만 기존 API에 반영한다.

- `CompanyProfile`: `business_type`, `license_codes`, `region_codes`, `annual_revenue`,
  `capacity_score`, `construction_capacity_amount`, `awarded_contract_limit`, `total_awards`.
- `OperatorStrategy`: `focus_categories`, `focus_regions`, `exclude_regions`, `required_keywords`,
  `exclude_keywords`, `min_budget_estimate`, `max_budget_estimate`.

후보가 추천 엔진에 반영되기 전에는 `draft` 상태로 둔다. 사용자가 수정하거나 거부한 후보도 저장해
다음 온보딩 규칙 개선에 사용한다.

### 4. 공고 미리보기 단계

확정 직후 `/api/v1/operator/strategy/candidates`를 호출해 현재 열린 공고 중 추천 후보를 보여준다.

후보가 있으면:

- 공고명, 금액, 마감일, 추천 투찰가, `matched_score`, `probability_score`, `strategy_reasons`를
  보여준다.
- "왜 가능한가"에는 면허, 지역, 예산, 업무구분, 키워드 일치 근거를 보여준다.

후보가 없으면:

- 후보 없음이 데이터 부족인지, 전략 과필터링인지, 면허/지역/예산 조건 과도 제한인지 구분한다.
- 사용자가 바로 넓힐 수 있는 조건을 제안한다. 예: 지역을 전국으로 넓히기, 금액 상한 조정,
  필수 키워드 제거.

## 데이터 모델 방향

신규 테이블 또는 필드 후보:

- `business_registration_number_hash`: 검색/중복 확인용 hash.
- `business_registration_number_encrypted`: 필요 시 암호화 원문. 운영 로그 출력 금지.
- `business_verification_status`: `unverified`, `verified`, `inactive`, `mismatch`, `unknown`.
- `business_verification_payload`: 원문이 아닌 masked/normalized 상태 payload.
- `onboarding_suggestions`: 후보별 source/confidence/reason/user_decision 기록.

기존 `CompanyProfile`에 모든 외부 원문을 넣지 않는다. 기존 프로필은 추천 엔진에 확정 입력으로만
사용하고, 후보/감사 정보는 별도 구조에 둔다.

## API 방향

- `POST /api/v1/operator/business-verification`: 사업자번호 상태조회/진위확인. 원문 번호는 응답에
  반환하지 않는다.
- `GET /api/v1/operator/onboarding-suggestions`: 현재 확인값과 프로필/전략 후보 반환.
- `POST /api/v1/operator/onboarding-suggestions/apply`: 사용자가 선택한 후보만
  `CompanyProfile`/`OperatorStrategy`에 부분 반영.
- 기존 `GET /api/v1/operator/strategy/candidates`: 확정 후 공고 미리보기 재사용.

## UI 방향

Phase 3 사용자 화면의 회사 정보 wizard에 "사업자번호로 시작" 단계를 추가한다.

화면 흐름:

1. 사업자번호 입력과 상태 확인.
2. 자동입력 후보 검토.
3. 면허/지역/금액/관심 조건 확정.
4. 현재 추천 공고 미리보기.
5. 알림 받을 후보 기준 저장.

자동입력 후보는 확정 전까지 visually distinct한 draft 상태로 표시한다. 사용자가 직접 입력한 값,
외부 API 확인값, 내부 추론 후보를 같은 확정값처럼 보이게 하지 않는다.

## 위험과 대응

- 국세청 API는 면허/지역/시공능력을 제공하지 않는다. 대응: 상태/진위 확인 용도로 한정한다.
- 사업자번호는 민감한 운영정보다. 대응: masking, hash/encryption, 로그 금지, evidence 원문 제외.
- 자동 추천 후보가 오탐일 수 있다. 대응: 사용자 확정 전에는 추천 엔진에 반영하지 않는다.
- 후보 없음이 제품 실패로 보일 수 있다. 대응: 후보 없음 원인을 조건 과필터링/데이터 부족/외부 확인
  필요로 분류해 다음 행동을 제시한다.

## 검증 기준

- 정상 사업자번호 입력 시 상태 확인 결과가 masked 상태로 표시된다.
- 폐업/휴업 또는 진위 불일치 시 추천 진행 전 명확한 warning이 표시된다.
- 사용자가 확정하지 않은 후보는 `CompanyProfile`/`OperatorStrategy`에 저장되지 않는다.
- 확정 후 후보 미리보기는 기존 `strategy_reasons`와 점수를 보여준다.
- 사업자번호 원문은 로그, analytics, G-2 evidence, notification payload에 남지 않는다.

## 로드맵 배치

이 기능은 G-2 exit 전 필수 개발이 아니다. G-2에서는 가상 사업자 증적 축적과 gap 해소를 우선한다.
사업자번호 기반 반자동 온보딩은 Phase 3 제한 실증 서비스의 온보딩/추천 UX 개선 항목으로 배치한다.
