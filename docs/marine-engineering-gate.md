# 해양엔지니어링협회 게이트

초기 고객인 **해양엔지니어링협회 가입 업체**(해양/항만 엔지니어링·기술용역 firm)를
대상으로, canonical operator를 이 아키타입으로 설정해 해양 공고를 추천/투찰가 산정
대상으로 식별하기 위한 게이트 정의 스펙이다.

로드맵 연계: 단일 운영자 검증(0~2단계) 위에서 **첫 실수요 고객 세그먼트 게이트**를
얹는 작업이며, G-3 실사용 검증 단계의 입력을 좁히는 용도다. Phase 2(KONEPS
면허제한 수집 보강)는 이후 단계다.

## 1. 게이트 정체성

- 게이트는 별도 계정이 아니라 **canonical `operator` 계정**(`app/core/single_user.py`)의
  `CompanyProfile` + `OperatorStrategy`를 해양 아키타입으로 설정한 것이다.
- 단일 대표 게이트다. synthetic-* 운영자와 무관하며 canonical operator를 오염시키지
  않는다(synthetic은 별도 seed).
- 금액 필드(`annual_revenue`/`capacity_score`/`construction_capacity_amount`/
  `awarded_contract_limit`)와 매칭 임계값(`minimum_match_score` 등)은 게이트가
  건드리지 않는다 — 기존/기본값을 유지한다.

## 2. 자격 기준

| 축 | 값 | 비고 |
|---|---|---|
| 카테고리 | `business_type = technical-service`, `focus_categories = technical-service, service, construction` | 기술용역 + 해양 공사(키워드 AND로 일반 공사 제외) |
| 면허 | 엔지니어링 / 항만및해안 / 해양엔지니어링 / 수로조사 | classifier가 ENG001/PORT001/MAR001/HYDRO001로 정규화 |
| 지역 | `region_codes = 전국` | 해양 공고는 연안 전역 분산 |
| 금액 | `min/max_budget_estimate = 0.0` | 무제한(금액 축으로 거르지 않음) |

## 3. 면허군

`app/services/classifier.py`의 `LICENSE_ALIASES`로 한글 면허명이 canonical 코드로
정규화된다. 시공(공사) 면허가 아니라 기술용역 면허군이다.

| 코드 | 한글 별칭 |
|---|---|
| `PORT001` | 항만및해안, 항만설계 |
| `MAR001` | 해양엔지니어링 |
| `HYDRO001` | 수로조사, 수로측량, 해양조사 |
| `ENG001` | 엔지니어링사업, 엔지니어링, 기술사, 감리 (기존) |

동작 메모:

- `해양엔지니어링`은 의도적으로 **ENG001 + MAR001 둘 다** 매칭된다 — `엔지니어링`이
  substring이라 ENG001이 함께 잡힌다. 해양엔지니어링 firm은 엔지니어링 firm이기도
  하므로 의도된 동작이다.
- `…기술사` 면허명(항만및해안기술사·해양기술사 등)은 기존 `기술사`(ENG001) 별칭으로
  ENG001과 함께 잡히고, 루트 별칭(`항만및해안`/`해양엔지니어링`)이 substring으로
  커버하므로 `…기술사` 접미 별칭은 두지 않는다(추출 결과 불변, 중복만 제거).
- bare 단어(`해양`/`항만`/`측량`/`해안`)는 과매칭이라 면허 별칭에서 제외한다
  (기존 건설 면허 별칭과 동일 원칙). 단, bare 어휘는 아래 키워드 게이트에서 다룬다.

## 4. Phase 1 키워드 게이트

즉효 게이트는 `OperatorStrategy.required_keywords`(OR 매칭) + `focus_categories`로
구성한다. 면허제한 미수집 공고도 키워드로 식별한다.

**키워드 매칭 범위**: `required_keywords`/`exclude_keywords`는 공고의
`title + requirements + category`에만 매칭한다(`description` 제외). KONEPS 수집기가
`description`에 공고기관·공고번호·URL 등 메타데이터를 적재하므로(`persistence.py`),
`description`까지 매칭하면 "공고기관: 해양수산부" 같은 기관명이 키워드를 오탐시킨다.
실제 작업명은 `title`, 작업요건은 `requirements`에 있어 recall 손실은 없다.
(`focus_regions`/`exclude_regions`는 지역이 메타에 있을 수 있어 full text 매칭 유지.)

키워드 31개(항만/해양/연안 토목·기술용역 어휘):

```
항만 어항 방파제 안벽 부두 잔교 선착장 물양장 호안 방조제 갑문 계류 케이슨
사석 해저 연안 해안 갯벌 조간대 해양조사 수로측량 조위 조류관측 해양환경
항만설계 해안침식 항로 항로표지 어초 인공어초 마리나
```

일반 어휘 6개(`해양`/`수산`/`해상`/`매립`/`등대`/`수중`)는 타도메인·기관명 오탐이
커서 제외했다(해양·수산→해양수산부 기관명, 해상→해상보험/운송, 매립→폐기물매립지,
등대→등대운영관리, 수중→수중생태조사). 구체 해양공학 합성어(해양조사/해양환경 등)는
유지한다. `준설`도 제외했다: 라이브 데이터에서 하수관로·저수지·배수로 준설공사(지자체
토목)를 대량 오탐하고, 진짜 해양 준설은 항만/항로/방파제/연안 등으로 잡힌다.

## 5. Phase 2 후속 (예정)

- **KONEPS 면허제한 수집 보강**: `lcnsLmtNm`/면허제한 상세를 수집해 공고별 요구
  면허를 확보한다.
- **`extract_license_codes` 한글/숫자코드 대응**: 면허제한 한글명과 KONEPS
  면허코드를 위 면허군에 매핑해, 키워드뿐 아니라 면허 요건으로도 해양 공고를
  정밀 식별한다.
- 키워드 게이트(Phase 1)와 면허제한(Phase 2)를 결합해 recall/precision을 함께 본다.

## 6. 적용 방법

```bash
# 변경 계획만 출력(commit 없음) — 기본 동작
python scripts/seed_marine_gate.py            # 또는 --dry-run

# canonical operator profile/strategy 에 실제 commit
python scripts/seed_marine_gate.py --apply
```

`--apply`는 멱등하다(절대값 설정). 운영 반영은 `--apply` 실행 후, 워커/비트가
바뀐 전략으로 추천/모니터링을 수행하면 된다(코드 변경이 아니므로 재빌드 불필요,
워커가 DB 전략을 매 실행 시 읽음).
