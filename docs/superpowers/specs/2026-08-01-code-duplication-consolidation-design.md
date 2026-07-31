# 코드 중복 통합 + 재발 방지 게이트 설계

- 작성일: 2026-08-01
- 상태: 설계 확정 (구현 계획 대기)
- 범위: `app/` · `scripts/` · `frontend/src/`

## 1. 문제

기능적으로 같은 헬퍼가 저장소 곳곳에 재구현돼 있다. 숫자 강제변환만 해도
`_coerce_float` · `_as_float` · `_safe_float` · `optional_float` · `_first_float` ·
`amount_float` · `_coerce_amount` 로 흩어져 있고, JSON 읽기/쓰기는 `scripts/` 안에서만
7벌이다.

핵심은 "룰이 없어서"가 아니다. **룰은 이미 있다.** CLAUDE.md §4.5-6:

> 같은 문제를 두 번째로 풀면 **공용 헬퍼/모듈로 추출**합니다. 복붙·중복 로직 금지.

그리고 이 통합은 **이미 한 번 수행된 적이 있다**. `scripts/_common.py` 의 docstring:

> "This module centralizes small CLI helpers that were **previously duplicated
> byte-for-byte** across several `scripts/*.py` entrypoints."

그 통합 이후에 `parse_thresholds`×2 · `parse_actions`×2 · `parse_csv`×2 ·
`_write_json`×4 · `_read_json_object`×3 · `_optional_int`×2 · `_count_lines`×3 이 새로
자랐다. **허브를 만들고 문서에 적는 것만으로는 유지되지 않는다는 것을 이 저장소가 이미
실증했다.** 따라서 이 설계의 무게중심은 정리가 아니라 **자동 게이트**다.

빠진 것은 둘이다.

1. **주소** — "어디를 먼저 찾고, 새 헬퍼를 어디에 두는가"가 문서에 없다.
2. **강제** — 위반을 기계가 잡지 않으니 사람 기억에 의존한다.

## 2. 실측

측정 방법은 §9 부록에 재현 절차를 남긴다.

### 2.1 전체 규모

| 축 | 수치 |
|---|---|
| Python (`app/` + `scripts/`) | 329 파일 · 70,127 줄 |
| Frontend (`frontend/src/`) | 277 파일 · 46,883 줄 |
| 스캔 대상 함수 (≥12 AST 노드) | 2,558 |
| AST 정규화 후 **구조 완전일치 클론 그룹** | 63 그룹 / 155 함수 |

### 2.2 판정 predicate 적용 후

"메커니컬 유틸리티"(도메인 지식 없는 함수) predicate 와 임계값을 적용한 결과:

| 임계값(AST 노드) | 클론 그룹 | 그룹 내 함수 | 교차파일 그룹 |
|---|---|---|---|
| 12 | 33 | 75 | 27 |
| **20** | **25** | **55** | **20** |
| 25 | 25 | 55 | 20 |
| 30 | 23 | 51 | 19 |
| 40 | 14 | 32 | 12 |

- **min 20~25 가 안정 고원**이다. 임계값 선택에 결과가 민감하지 않다는 뜻이고, 래칫
  지표로 쓰기에 적합한 성질이다. 고원의 하단인 **20** 을 채택한다.
- **위양성 0**: `app/api/operator.py` 의 얼짜 FastAPI 라우터 7개는 구조가 동일하지만
  데코레이터 배제만으로 그룹이 0개가 된다. 이들은 CLAUDE.md §4 "라우터는 얇게" 를 지킨
  결과이므로 통합 대상이 아니며, predicate 가 이를 정확히 걸러낸다.
- **진짜 중복은 포착**: `_write_json`×4 · `_read_json_object`×3 이 min=20 에서 모두
  그룹으로 잡힌다.

### 2.3 프론트엔드

프론트는 아직 스캐너가 없어 수동 grep 기반 추정이다(정확한 목록은 PR4 가 확정한다).

- `frontend/src/shared/lib.ts` 에 포맷터 허브가 이미 있는데도 feature 로컬 재구현이 있다:
  `rateOrDash`×3 · `formatNullablePercent`×3 · `formatCount`×2 · `formatWon` ·
  `formatRevenue`
- `toLocaleString` / `toFixed` / `Intl.*` 직접 호출이 **15개 파일**에 흩어져 있다
- `shared/lib.ts` 자체가 포맷터 + 라벨 + 상태매핑 + `cn` 이 섞인 그랩백이다

### 2.4 현재 강제 지점

| 영역 | 현재 CI |
|---|---|
| `app/` | ruff · mypy · pytest (설계 래칫 게이트 포함) |
| `scripts/` | pytest 만 (ruff 는 래칫 스캐너 2개 모듈만, mypy 제외 — grandfathered) |
| `frontend/` | **없음** — `.github/workflows/ci.yml` 에 프론트 job 자체가 없다. ESLint 설정 파일도 없다 |

## 3. 결정 사항

| 항목 | 결정 |
|---|---|
| 범위 | `app/` + `scripts/` + `frontend/` 전부 |
| 중복의 정의 | 순수 헬퍼·메커니컬 유틸리티만. 도메인 로직·프레임워크 보일러플레이트는 대상 아님 |
| 강제 수단 | 기존 design ratchet 에 지표 신설 + 문서 룰 |
| 통합 목적지 | 주제별 작은 모듈 허브 (단일 거대 모듈 아님) |
| 프론트 게이트 | vitest 래칫 미러링 + CI 프론트 job 신설 |
| 실행 순서 | 게이트 우선 · 5 PR |

## 4. 측정 코어 — `duplicate_mechanical_helpers`

### 4.1 지표 정의

```
duplicate_mechanical_helpers[파일] =
  그 파일이 정의한 메커니컬 헬퍼 중,
  다른 파일의 메커니컬 헬퍼와 구조 클론 그룹을 이루는 함수의 수
```

**교차 파일 그룹만 센다.** 동일 파일 내 클론 5그룹
(`_int_or_none`/`_float_or_none`, `is_openapi_source`/`is_scsbid_openapi_source`,
`coerce_numeric_list`/`coerce_integer_list`,
`_resolve_goods_procurement_rate_band`/`_resolve_service_procurement_rate_band`,
`extract_eligibility_flags`/`_project_license_limit_item`) 은 제외한다.

이유: (a) 의도적 대칭 API 인 경우가 많아 강제 통합이 가독성을 해친다, (b) 이 작업이
겨냥하는 문제는 "여기저기 산발적"이라는 교차 파일 현상이다, (c) 래칫이 파일 단위로
귀속하므로 교차 파일이 자연스러운 단위다.

이는 기존 `env_test_sniff` 지표와 같은 성격의 **의도적 한정**이며, 총량 지표가 아니라
"가장 흔한 형태의 증가를 막는 래칫"이다. 미탐 범위는 §8 에 명시한다.

### 4.2 판정 파이프라인

| 단계 | 규칙 |
|---|---|
| 1. 후보 | 함수/메서드 중 **데코레이터가 하나도 없는** 것. 라우터·celery task·`property`·`staticmethod` 가 전부 배제된다 |
| 2. 메커니컬 | 본문에 `MECHANICAL_EXCLUDED_NAMES` · `MECHANICAL_EXCLUDED_ATTRS` 가 없고 `global`/`nonlocal` 이 없음 |
| 3. 크기 | AST 노드 수 ≥ `CLONE_MIN_AST_NODES` (= 20) |
| 4. 정규화 | 변수·인자명 → 등장순 `v0,v1,…` · docstring/데코레이터/annotation/반환타입 제거 · **상수 리터럴은 보존** |
| 5. 그룹핑 | `sha1(ast.unparse(정규화))[:12]` 가 동일하고 소속 **파일이 2개 이상** |

배제 목록 초안(스캐너 상단 선언 상수로 고정):

```python
MECHANICAL_EXCLUDED_NAMES = frozenset({
    "self", "cls",
    "db", "session", "Session", "engine", "select", "func",
    "requests", "httpx", "urllib", "socket",
    "subprocess", "sys", "shutil",
    "logger", "logging",
    "settings",
    "input", "print",
})
MECHANICAL_EXCLUDED_ATTRS = frozenset({
    "query", "commit", "add", "execute", "flush", "refresh", "scalar", "scalars",
})
CLONE_MIN_AST_NODES = 20
```

`open` · `Path` · `json` · `datetime` 은 **배제하지 않는다**. 얇은 메커니컬 I/O 래퍼
(`_write_json` 류)는 통합 대상이 맞기 때문이다. 이 판단이 predicate 를 "순수 함수"가
아니라 "메커니컬 유틸리티"로 정의하는 이유다.

정규화의 두 귀결을 문서화한다.

- 상수 리터럴을 보존하므로 `round(x, 2)` 와 `round(x, 3)` 은 **다른 그룹**이다.
  보수적 판정이라 위양성이 낮다.
- annotation 을 제거하므로 **타입만 다른 쌍은 같은 그룹**이 된다. 이는 제네릭 통합
  후보가 맞으므로 의도된 동작이다.

### 4.3 스캐너 아키텍처

`scan_source(relative_path, source) -> FileMetrics` 는 파일 단위 순수 함수라 교차 파일
정보를 가질 수 없다. 이 계약을 깨지 않고 확장한다.

```python
# 신규: 파일 단위 순수 함수 (기존 스타일 유지)
def collect_clone_signatures(relative_path: str, source: str) -> list[CloneSignature]: ...

# scan_repo 를 2-pass 로
def scan_repo(root: Path) -> RatchetReport:
    # pass 1: 파일별 scan_source(...)  +  collect_clone_signatures(...)
    # pass 2: 시그니처 그룹핑 → 교차파일 그룹 → 파일별 카운트를 FileMetrics 에 병합
    ...
```

`FileMetrics` 에 `duplicate_mechanical_helpers: int = 0` 을 추가한다. baseline JSON 은
값이 0 인 지표를 생략하는 sparse 형식이므로 **기존 baseline 과 스키마 호환**이고
`_is_corrupt_baseline` 경로를 타지 않는다. 그럼에도 PR1 에서 baseline 은 새 지표 값을
기록하도록 재생성한다.

파싱 실패는 기존 계약대로 `RatchetScanError` 로 크게 실패한다. 조용히 스킵하면 그 파일의
위반이 0 으로 보고되어 "파일 삭제 = 통과" 구멍이 생긴다.

### 4.4 래칫 동작 검증

| 시나리오 | 동작 |
|---|---|
| 새 파일이 허브 헬퍼를 복붙 | 새 파일은 baseline 에 없어 0 으로 취급 → **즉시 위반**. 예방의 핵심 |
| 기존 허브 함수를 어딘가로 복사 | 허브 파일 카운트가 0→1 로 상승 → **위반** |
| 그룹을 완전 통합 | 그룹의 모든 파일이 동시에 0 으로 하락 → 통과 |
| 부분 통합 (4곳 중 1곳만 제거) | 남은 3곳은 각 1 유지 → 감소 없음. **위반도 아님**(증가가 아니므로) |

마지막 행은 수용 가능한 성질이지만 **부분 개선이 baseline 에 반영되지 않는다**는 점을
§8 에 한계로 명시한다.

### 4.5 튜닝 표면 고정

배제 목록을 바꾸자 후보 함수 수가 1,148 → 1,012 로 움직이는 것을 관측했다. 이 목록은
지표값을 직접 좌우하는 **튜닝 표면**이므로 CLAUDE.md §4.5-3(특수케이스는 데이터로 선언)
에 따라 고정한다.

- 배제 목록·임계값은 스캐너 상단 **선언 상수** (기존 7개 지표와 동일 스타일)
- `CLONE_ALLOWLIST` 상수로 의도적 예외를 사유와 함께 선언 (기존 `JSON_CALL_ALLOWLIST`
  패턴 재사용)
- **라벨링된 픽스처**로 predicate 를 양방향 고정 (§7.1)

## 5. 통합 작업

### 5.1 목적지 구조

모듈에 적은 함수는 **지표가 실제로 잡은 통합 대상**(§5.2)뿐이다. 허브는 앞으로 생길
헬퍼의 주소이기도 하지만, 이 설계의 작업 범위는 측정된 것에 한정한다.

```
app/utils/                       app/ · scripts/ 공통 메커니컬 허브
  numeric.py                     coerce_float · coerce_amount · optional_float · optional_int
  jsonio.py                      read_json_object · write_json · parse_json_or_text
  textfmt.py                     clip_title
  sequence_coercion.py           (기존)

scripts/_common.py → scripts/_common/    stdlib-only CLI 전용
  __init__.py                    기존 parse_datetime · positive_int 재수출 (import 보존)
  cliargs.py                     parse_thresholds · parse_actions · parse_csv
  report.py                      count_lines · ordered_segments · verdict_line · label_for

frontend/src/shared/
  format/
    number.ts                    formatPercent · formatCurrency · formatCurrencyCompact
                                 formatCount · rateOrDash · formatNullablePercent
    date.ts                      formatDate · formatDateTime · formatRelativeTime · formatHours
    index.ts
  lib.ts                         cn + 라벨/상태 매핑만 (shared/format 재수출로 기존 import 보존)
```

**`scripts/_common/` 이 `app/utils/` 를 재수출하지 않는 것**이 중요하다.
`scripts/_common.py` 는 docstring 에서 "app 의존성을 끌어오지 않는 stdlib-only" 를
명시적으로 약속하므로 재수출은 그 계약을 깬다. 대신 `app/__init__.py` 와
`app/utils/__init__.py` 가 docstring 뿐이고(부수효과 없음) 스크립트들이 이미
`from app.services... import` 를 하고 있으므로, **스크립트는 `app.utils.*` 를 직접
import** 한다. `scripts/_common/` 은 stdlib-only CLI 헬퍼만 유지한다.

### 5.2 통합 매핑 (교차파일 20그룹)

| PR | 그룹 | 대상 | 목적지 |
|---|---|---|---|
| PR2 | G15·G16 | `optional_float` · `_safe_optional_int` · `_coerce_amount` · `_coerce_float` · `amount_float` | `app/utils/numeric.py` |
| PR2 | G03·G14 | `_memberships_by_group`/`_tech_fields_by_group` · `_format_groups`×2 | `app/services/classification/_grouping.py` |
| PR2 | G20 | `normalize_agency_name`×2 | `koneps/parsing.py` canonical → `predictors/historical/statistics.py` 가 import |
| PR2 | G19 | `guidance_for`/`failure_guidance` | `smoke_failure_taxonomy.py` canonical → `scripts/production_smoke_test.py` 가 import |
| PR3 | G02·G10·G22 | `_read_json_object`×3 · `_write_json`×4 · `parse_json_or_text`×2 | `app/utils/jsonio.py` |
| PR3 | G07 | `_optional_int`×2 | `app/utils/numeric.py` |
| PR3 | G04·G09·G12 | `parse_thresholds`×2 · `parse_actions`×2 · `parse_csv`×2 | `scripts/_common/cliargs.py` |
| PR3 | G11 | `_clip_title`×2 | `app/utils/textfmt.py` |
| PR3 | G08·G05·G17·G23 | `_count_lines`×3 · `_ordered_segments`×2 · `_verdict_line`×2 · `*_label_for`×2 | `scripts/_common/report.py` |

합계: PR2 6그룹 · PR3 12그룹 · 보류 2그룹 = **교차파일 20그룹 전부**를 다룬다.

`G23` (`_classify_label_for` / `_segment_label_for`) 은 이름이 다르므로 통합 전에 두
함수의 **의미가 실제로 같은지** 확인한다. 다르면 allowlist 로 옮긴다.

### 5.2.1 지표 임계값 아래의 중복

`_average`×4 · `_round_optional`×2 · `_csv_safe`×2 처럼 **AST 20노드 미만이라 지표가 잡지
않는** 중복이 따로 있다. 해당 파일을 어차피 건드리는 PR 에서는 같이 정리하되, §7.4 의
성공 기준에는 넣지 않는다. 지표가 세지 않는 것을 목표로 잡으면 검증할 수 없기 때문이다.

### 5.3 통합하지 않고 보류하는 2건

20그룹 전부가 통합이 옳은 것은 아니다. 다음 둘은 `CLONE_ALLOWLIST` 에 **사유와 함께
등재**하고 코드를 건드리지 않는다.

- **G13** — `app/services/award_verification.py:158 _rate_to_fraction` /
  `app/services/base_amount_basis.py:64 normalize_winning_rate`.
  구조는 같지만 **금액 basis 도메인**이다. 이 저장소에서 basis 혼동은 반복 버그의
  근본원인으로 지목된 영역이므로, 두 함수의 basis 의미가 동일함을 증명하기 전에 합치면
  버그를 만든다.
- **G24** — `app/ai/predictors/registry.py:13 build_default_predictor_registry` /
  `scripts/backtest_price_predictors.py:56 build_registry`.
  CLAUDE.md §4.7-2 팩토리/레지스트리 패턴이다. 스크립트가 **축소 레지스트리를 주입**하는
  것이 설계 의도일 수 있어, 통합하면 테스트 격리 seam 이 사라진다.

### 5.4 프론트엔드 (PR4·PR5)

**PR4 — vitest 래칫 + CI 프론트 job**

```
frontend/scripts/designRatchet.ts               TypeScript 컴파일러 API 기반 스캔
frontend/tests/design-ratchet.baseline.json     baseline
frontend/src/__tests__/designRatchet.test.ts    vitest 게이트
```

`typescript ^6.0.3` 이 이미 devDependency 이므로 **정규식이 아니라 진짜 AST** 를 쓴다.
파이썬 쪽과 동등한 정확도가 나오고, 신규 의존성이 0 이다.

파이썬과 **동일한 멘탈모델**을 유지한다: 같은 지표명, 같은 파일별 계수, 같은 "증가만
차단" 계약, 같은 `--update-baseline` delta 출력. 두 영역을 따로 배울 필요가 없게 한다.

TS 고유의 판정 차이 — 데코레이터 배제 대신:

- **React 컴포넌트 배제**: 대문자로 시작하고 JSX 를 반환하는 함수
- **훅 배제**: `use` 접두 함수, 또는 본문에 훅 호출을 포함하는 함수

컴포넌트와 훅은 구조가 서로 비슷한 것이 정상이므로 위양성의 주 원인이 된다.

CI 에 프론트 job 을 신설한다: `tsc --noEmit` · `vitest run` · `vite build`. 현재 CI 가
프론트를 전혀 돌리지 않으므로 **중복 게이트와 무관하게 독립적인 이득**이다.

**PR5 — `shared/format/` 신설 + `lib.ts` 그랩백 해소**

흡수 대상은 §2.3 의 목록이되, **정확한 목록은 PR4 스캐너 리포트가 확정**한다. 기존
`@/shared/lib` import 는 `lib.ts` 가 `shared/format` 을 재수출해 전부 보존하고, 호출부
이전은 점진적으로 한다.

## 6. 룰

### 6.1 CLAUDE.md §4.5-8 신설 — 중복 금지

§4.5 에 독립 조항을 신설한다. §4.5-6(패턴 활용)은 "기존 패턴을 따르라"는 지향이고,
신설 조항은 "중복을 만들지 말라 + 어디에 두라 + 무엇이 막느냐"는 집행 규칙이다.

조항이 담을 내용:

- 메커니컬 헬퍼(숫자 변환·JSON I/O·문자열 포맷·CLI 인자 파싱)는 **허브에만 정의**한다
- 새 헬퍼를 쓰기 전에 **허브를 먼저 grep** 한다
- 교차 파일 중복은 `duplicate_mechanical_helpers` 래칫이 **자동 차단**한다. 이 지표가
  오르는 PR 은 CI 에서 실패한다
- 의도적 예외는 `CLONE_ALLOWLIST` 에 **사유와 함께** 등재한다 (주석으로 넘기지 않는다)

### 6.2 CLAUDE.md §4.5-6 보강 — 허브 주소표

산문 룰은 그대로 두고 주소표를 붙인다.

| 용도 | 허브 |
|---|---|
| 숫자 변환·집계 | `app/utils/numeric.py` |
| JSON 읽기/쓰기 | `app/utils/jsonio.py` |
| 문자열·포맷 | `app/utils/textfmt.py` |
| 시퀀스 강제변환 | `app/utils/sequence_coercion.py` |
| 시간 | `app/core/time.py` |
| 교차 모듈 도메인 상수 | `app/core/constants.py` |
| 런타임·환경 설정 | `app/core/config.py` |
| CLI 인자 (stdlib-only) | `scripts/_common/cliargs.py` |
| 프론트 포맷 | `@/shared/format` |

### 6.3 그 외 문서

| 문서 | 변경 |
|---|---|
| `CLAUDE.md` §9 | PR 체크리스트에 `python scripts/design_ratchet.py` 통과 + 프론트 래칫 통과 항목 추가 |
| `docs/operations/design-ratchet.md` | 지표표에 `duplicate_mechanical_helpers` 행 · 판정 파이프라인 · 미탐 범위(§8) · 프론트 래칫 절 신설 |

## 7. 테스트 · 에러 처리 · 성공 기준

### 7.1 스캐너 테스트 (PR1 · PR4)

픽스처를 **양방향으로** 고정한다. 배제 목록이나 임계값을 건드리면 이 픽스처가 깨지므로
튜닝이 조용히 드리프트하지 못한다.

**반드시 잡아야 할 것**

- `_write_json` 류 교차파일 4벌
- `_read_json_object` 류 교차파일 3벌
- 이름만 다르고 구조가 같은 쌍 (`_coerce_float` / `amount_float`)

**반드시 놓쳐야 할 것**

- 데코레이터가 붙은 얼짜 FastAPI 라우터 (`app/api/operator.py` 패턴)
- DB 세션에 접근하는 서비스 메서드
- 동일 파일 내 대칭 API (`_int_or_none` / `_float_or_none`)
- 상수 리터럴만 다른 쌍 (`round(x, 2)` vs `round(x, 3)`)
- (TS) React 컴포넌트와 커스텀 훅

기존 `compare_reports` 계약 테스트와 "저장소 스캔이 baseline 을 초과하지 않음" 통합
테스트는 그대로 재사용한다.

### 7.2 통합 테스트 (PR2 · PR3 · PR5)

통합은 리팩터이므로 **동작 변경 0 을 증명**한다. 이 저장소의 기존 관례(특성화 테스트
선행 → 출력 동치 확인)를 따른다.

1. 통합 전에 대상 함수의 특성화 테스트를 먼저 붙인다 (없으면 추가)
2. 통합 후 동일 입력 → 동일 출력을 확인한다
3. 통합하면서 새 동작을 끼워 넣지 않는다

`scripts/` 는 CI 의 ruff·mypy 대상이 아니지만(grandfathered) 대상 스크립트 대부분에
테스트가 있다: `test_scripts_common.py` · `test_build_g2_exit_review.py` ·
`test_check_g2_exit_readiness.py` · `test_collect_g2_evidence.py` ·
`test_verify_g2_notification_targets.py` · `test_report_license_gate_impact.py` ·
`test_report_no_candidate_cause.py` · `test_report_eligibility_segment_backtest.py` 등.

### 7.3 에러 처리

| 상황 | 동작 |
|---|---|
| 소스 파싱 실패 | `RatchetScanError` 로 크게 실패 (기존 계약). 조용한 스킵은 "파일 삭제 = 통과" 구멍 |
| baseline 스키마 불일치 | 기존 `_is_corrupt_baseline` 안내 경로 |
| **죽은 allowlist 항목** | `CLONE_ALLOWLIST` 에 등재됐으나 현재 스캔에 더 이상 존재하지 않는 키가 있으면 **실패**시킨다 |

죽은 allowlist 차단은 기존 `JSON_CALL_ALLOWLIST` 에는 없는 검사다. 이것이 없으면
allowlist 가 stale 해지면서 면제 범위가 조용히 넓어진다.

### 7.4 성공 기준

| 시점 | 파이썬 교차파일 클론 |
|---|---|
| 현재 | 20 그룹 / 45 함수 |
| PR1 완료 | 동일 (baseline 동결 — 이 시점부터 증가 차단) |
| PR3 완료 | **2 그룹 / 4 함수** (allowlist 등재한 G13 · G24 만 잔존) |

프론트 목표치는 PR4 스캐너 리포트가 확정한다. 현재 프론트 수치는 수동 grep 추정이므로
목표로 박지 않는다.

## 8. 범위 밖 · 알려진 한계

이 설계가 **잡지 못하는 것**을 명시한다. 지표는 총량 측정이 아니라 증가 차단 래칫이다.

- **동일 파일 내 클론** — 계수하지 않는다 (§4.1). 현재 5그룹이 여기 해당한다.
- **의미적 유사(near-duplicate)** — 구조가 다르면서 같은 일을 하는 함수는 잡히지 않는다.
  구조 완전일치만 판정한다.
- **부분 통합** — 4곳 중 1곳만 제거하면 지표가 줄지 않는다 (§4.4).
- **도메인 로직 중복** — predicate 가 도메인 결합 함수를 배제하므로 대상이 아니다.
- **클래스·타입·상수의 중복** — 함수만 스캔한다.
- **ESLint 도입** — 프론트에 표준 린터를 들이는 것은 별개 결정이며 이 설계에 포함하지
  않는다.
- **`scripts/` 를 ruff·mypy CI 대상으로 승격** — grandfathered 상태를 유지한다.

## 9. 실행 계획

| PR | 내용 | 게이트 효과 |
|---|---|---|
| **PR1** | `_design_ratchet_scan.py` 2-pass + predicate 선언 상수 + 양방향 픽스처 · baseline 현재값 동결 · CLAUDE.md §4.5-8 신설/§4.5-6 주소표/§9 체크리스트 · `docs/operations/design-ratchet.md` 갱신 | 파이썬 신규 중복 차단 시작 |
| **PR2** | `app/` 통합 — `app/utils/numeric.py` · `classification/_grouping.py` · canonical 지정 2건 + baseline 갱신 | — |
| **PR3** | `scripts/` 통합 — `app/utils/jsonio.py` · `scripts/_common/` 패키지화(`cliargs.py`·`report.py`) + baseline 갱신 | 목표치 도달 |
| **PR4** | 프론트 래칫(`designRatchet.ts` + vitest 게이트) + CI 프론트 job 신설 · baseline 현재값 동결 | 프론트 신규 중복 차단 시작 |
| **PR5** | `frontend/src/shared/format/` 신설 · `lib.ts` 그랩백 해소 + baseline 갱신 | — |

각 PR 은 CLAUDE.md §10 워크플로(별도 worktree + feature branch → PR → 리뷰 → 사용자 머지
승인)를 따른다.

## 10. 부록 — 측정 재현

이 문서의 수치는 다음 절차로 재현한다.

1. `app/` · `scripts/` 의 `*.py` 를 `ast.parse` (`__pycache__` 제외)
2. 함수/비동기함수 노드마다 AST 노드 수를 세고 `CLONE_MIN_AST_NODES` 미만은 제외
3. §4.2 의 predicate 로 메커니컬 여부 판정
4. `ast.unparse` → `ast.parse` 로 복제한 뒤 `name="f"` · `decorator_list=[]` ·
   `returns=None`, 첫 문장이 문자열 상수 `Expr`(docstring)이면 제거
5. `NodeTransformer` 로 `ast.Name.id` 와 `ast.arg.arg` 를 등장 순서대로 `v0,v1,…` 치환,
   `arg.annotation=None`
6. `sha1(ast.unparse(정규화))[:12]` 를 그룹 키로 사용
7. 소속 파일이 2개 이상인 그룹만 남긴다

PR1 이후에는 `python scripts/design_ratchet.py` 가 이 절차를 수행하고 리포트를 낸다.
