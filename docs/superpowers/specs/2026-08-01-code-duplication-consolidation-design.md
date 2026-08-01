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
| Python (`app/` + `scripts/`) | 379 파일 · 87,734 줄 |
| Frontend (`frontend/src/`) | 277 파일 · 46,894 줄 |

predicate 적용 전의 원시 클론 수는 §4.2 의 판정을 거치지 않은 값이라 운용 의미가 없어
싣지 않는다. **운영 수치는 §2.2 이고, 함수 총계도 그쪽이 유일한 기준이다** — 같은
수치를 두 곳에 적으면 갈라지므로 여기서는 함수 총계를 인용하지 않는다.

### 2.2 판정 predicate 적용 후

PR1 구현 시점(`8f8f1e8`) 기준으로 `app/` · `scripts/` 의 **379 파일 · 87,734 줄 ·
함수 2,669 개**를 스캔했고 파싱 실패는 0 이다. 함수 열거는 `ast.walk` 로 **메서드와
중첩 함수까지** 포함한다(§10). 여기에 "메커니컬 유틸리티"(도메인 지식 없는 함수)
predicate 와 임계값을 적용한 결과다.

**측정 기준이 바뀌었다.** 노드 수는 **정규화 후** 형태에서 재고(§4.2 3·5단계), 순수
파라미터 위임은 후보에서 제외한다(§4.2 4단계). 그래서 아래 수치는 구 표(원본 노드 수
기준)와 직접 비교할 수 없다. 임계값의 절대값도 의미가 달라져 20 → **14** 로 내렸다.

| 임계값(정규화 AST 노드) | 후보 함수 | 클론 그룹 | 그룹 내 함수 | 교차파일 그룹 | 동일파일 그룹 |
|---|---|---|---|---|---|
| 6 | 1,291 | 42 | 95 | 36 | 6 |
| 8 | 1,288 | 42 | 95 | 36 | 6 |
| 10 | 1,280 | 41 | 92 | 35 | 6 |
| 12 | 1,259 | 37 | 81 | 32 | 5 |
| **14** | **1,228** | **36** | **79** | **31** | **5** |
| 16 | 1,195 | 33 | 73 | 28 | 5 |
| 18 | 1,175 | 32 | 71 | 27 | 5 |
| 20 | 1,139 | 30 | 67 | 26 | 4 |

(그룹 수는 `CLONE_ALLOWLIST` 적용 **전** 원시 값이다. 14 에서 allowlist 2그룹·4함수를
빼면 래칫이 기록하는 값은 교차파일 29그룹 / 65함수 · 동일파일 5그룹 / 10함수다.)

**14 를 고른 근거는 위아래 양쪽 경계를 실측으로 확인한 것이다.**

- **아래로 12 이하는 우연의 일치가 섞인다.** 12 에서만 잡히고 14 에서 사라지는 그룹은
  정확히 하나이고, 그것이 이 스윕 전체에서 유일한 우연 일치다 —
  `app/services/license_eligibility.py:parse_license_limit_groups` ↔
  `scripts/_design_ratchet_scan.py:scan_repo`(12노드). 도메인이 전혀 다른 두 함수가
  "루프 안에서 조건부로 리스트에 append" 라는 골격만 같다. 10 이하로 더 내리면
  `app/core/time.py` 의 `kst_now`/`utc_now`(10노드) 처럼 **의도적으로 대칭인 쌍**과
  `_is_pbkdf2_hash` ↔ `is_custom_slug`(11노드) 같은 `startswith` 우연 일치가 추가된다.
- **위로 16 이상은 진짜 중복을 잃는다.** 14~15 구간에서만 잡히는 3그룹이 전부 실제
  복붙이다 — `_mapping_or_empty` ↔ `_as_dict`(15, 아래 "게이트 필요성의 직접 증거"가
  가리키는 바로 그 신규 중복) · `_email_for` ↔ `_email`(15) · `format_threshold` ↔
  `_format_threshold`(14).
- **18 이상은 게이트를 깨뜨린다.** `CLONE_ALLOWLIST` 의
  `build_default_predictor_registry` ↔ `build_registry` 는 정규화 16노드다. 임계값을
  18 이나 20 으로 두면 이 그룹이 후보에서 사라져 `unused_allowlist_keys` 가 죽은 항목
  으로 신고하고 **CLI 와 pytest 게이트가 실패한다**. 20 은 §4.1 이 열거한 동일파일
  5쌍 중 `_resolve_goods_procurement_rate_band` 쌍(18노드)까지 잃는다.
- **§7.1 의 "반드시 잡아야 할" 목록이 14 에서 전부 살아남는다.** 교차파일 27종 대표
  함수와 동일파일 5쌍을 실측으로 확인했다. **임계값의 정당성은 픽스처가 진다.**
- **얼짜 라우터 위양성은 여전히 0 이다.** 데코레이터 배제만으로 `app/api/operator.py` 의
  FastAPI 엔드포인트 7개가 전부 걸러진다. 이들은 CLAUDE.md §4 "라우터는 얇게" 를 지킨
  결과이므로 통합 대상이 아니며, predicate 가 이를 정확히 걸러낸다.
- **다만 "`app/api/` 하위는 전부 위양성"은 아니다.** `_with_current_operator` 가
  `app/api/analytics.py:63` 과 `app/api/decision_samples.py:34` 에 2벌 있다. 데코레이터가
  없는 진짜 중복 헬퍼이고(본문 동일, 한쪽에만 docstring) 통합 대상이 맞다.
- **진짜 중복은 전부 포착**: `_write_json`×5 · `_read_json_object`×3 · `_count_lines`×3
  등 §7.1 이 "반드시 잡아야 할 것"으로 고정하는 대표 함수가 모두 그룹으로 잡힌다.
  단 `_average`×6 은 **더 이상 대상이 아니다** — 여섯 곳 모두 이미 공용
  `average()` 로 위임을 마친 얇은 명명 래퍼라 §4.2 4단계가 배제한다.
- **게이트 필요성의 직접 증거**: 최초 측정(`db57f8c`) 이후 5일 만에 머지된 PR #337~#341
  은 기존 중복을 **0건** 없앴고, 오히려 **새 중복 1건을 들여왔다** —
  `app/ai/predictors/artifact_contracts.py:106 _mapping_or_empty` ↔
  `scripts/check_g2_exit_readiness.py:126 _as_dict`. 같은 스크립트를 두 커밋에 각각 돌린
  apples-to-apples 델타가 35→36 그룹(+1)이다.

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
| 강제 수단 | 기존 design ratchet 에 지표 **2개** 신설(교차파일 · 동일파일) + 문서 룰 |
| 동일파일 클론 | 면제하지 않는다. 5그룹 전수 확인 결과 전부 "상수·캐스트만 다른 동일 알고리즘"이라 파라미터화로 환원 가능 (§4.1) |
| 통합 목적지 | 주제별 작은 모듈 허브 (단일 거대 모듈 아님) |
| 프론트 게이트 | vitest 래칫 미러링 + CI 프론트 job 신설 |
| 실행 순서 | 게이트 우선 · 5 PR |

## 4. 측정 코어 — `duplicate_mechanical_helpers` / `…_local`

### 4.1 지표 정의 — 두 축

동일 파일 클론과 교차 파일 클론은 **처방이 다르므로** 지표를 나눈다. 기존 래칫이
`functions_over_soft_limit` / `functions_over_hard_limit` 를 같은 축에서 두 지표로 나눈
선례를 따른다.

```
duplicate_mechanical_helpers[파일] =
  그 파일이 정의한 메커니컬 헬퍼 중,
  **다른 파일**의 메커니컬 헬퍼와 구조 클론 그룹을 이루는 함수의 수
  → 처방: 허브로 이동

duplicate_mechanical_helpers_local[파일] =
  그 파일 **안에서만** 서로 구조 클론 그룹을 이루는 메커니컬 헬퍼의 수
  → 처방: 파라미터화된 해석기 + 얇은 명명 래퍼
```

동일 파일 클론을 계수하는 근거는 실측이다. 현재 5그룹을 전수 확인한 결과 **어느 것도
"환원 불가능한 의도적 대칭 API" 가 아니었다.** 5그룹 모두 같은 형태다 — *알고리즘은
동일하고 선언된 상수 하나 또는 캐스트 함수 하나만 다르다.*

| 그룹 (동일 파일 내 쌍) | 다른 점 | 처방 |
|---|---|---|
| `app/services/koneps/openapi.py:289 extract_eligibility_flags` / `:307 _project_license_limit_item` | 키 집합 상수 (`ELIGIBILITY_RAW_KEYS` / `LICENSE_LIMIT_ITEM_KEYS`) | `project_declared_keys(raw_item, keys)` |
| `app/utils/sequence_coercion.py:34 coerce_numeric_list` / `:46 coerce_integer_list` | 캐스트 (`float` / `int`) | `_coerce_cast_list(raw_value, cast)` |
| `app/services/synthetic_backtest.py:251 _int_or_none` / `:258 _float_or_none` | 캐스트 (`int` / `float`) | `_cast_or_none(value, cast)` |
| `app/services/koneps/openapi.py:41 is_openapi_source` / `:46 is_scsbid_openapi_source` | 별칭 집합 상수 | `_source_in(source, aliases)` |
| `app/ai/predictors/historical/procurement_bands.py:55 _resolve_goods_procurement_rate_band` / `:63 _resolve_service_procurement_rate_band` | 룰 테이블 (`GOODS_BAND_RULES` / `SERVICE_BAND_RULES`) | `resolve_band(rules, …)` 직접 호출 |

이는 CLAUDE.md §4.5-1·2·3 이 말하는 **"규칙은 데이터로, 코드는 해석기만"** 그 자체다.
특히 `extract_eligibility_flags` / `_project_license_limit_item` 쌍은 **정규화 68노드**
(원본 93노드)로 가장 큰 동일파일 클론이고 이름과 docstring 이 서로 다른 **진짜 복붙**
이다. 동일파일을 면제하면 지표가 가장 잡아야 할 종류를 놓친다.

**가독성 우려는 판정 파이프라인이 자동으로 해소한다.** 공개 이름(`_int_or_none` ·
`_float_or_none`)은 얇은 델리게이터로 남길 수 있고, 그 델리게이터는 **두 겹으로**
계수에서 빠진다.

1. **위임 배제(§4.2 4단계)** — 본문이 `return _cast_or_none(value, int)` 한 줄이면
   노드 수와 무관하게 후보에서 제외된다. 타입힌트와 docstring 을 붙여도 마찬가지다.
2. **크기(§4.2 5단계)** — 크기는 **정규화 후** 형태에서 재므로 델리게이터는 실측
   10~12 노드로 `CLONE_MIN_AST_NODES`(14) 아래다.

두 번째 항목이 성립하려면 크기를 정규화 후에 재야 한다는 점이 중요하다. 원본에서 재면
`def _average(self, values: list[int | float]) -> float | None:` + docstring 이 붙은
델리게이터가 29노드로 임계값을 넘어 **처방의 산출물이 위반으로 계수된다**. 실제로
PR1 초기 구현이 이 상태였고, 그래서 이미 통합을 마친 `_average`×6 이 baseline 에
오탐으로 동결됐다(§7.4). 기준을 정규화 후로 맞추면 이름의 가독성을 지키면서 중복은
사라지며, **allowlist 항목이 0개** 필요하다.

### 4.2 판정 파이프라인

| 단계 | 규칙 |
|---|---|
| 1. 후보 | 함수/메서드 중 **데코레이터가 하나도 없는** 것. 라우터·celery task·`property`·`staticmethod` 가 전부 배제된다 |
| 2. 메커니컬 | 본문에 `MECHANICAL_EXCLUDED_NAMES` · `MECHANICAL_EXCLUDED_ATTRS` 가 없고 `global`/`nonlocal` 이 없음 |
| 3. 정규화 | 변수·인자명 → 등장순 `v0,v1,…` · docstring/데코레이터/annotation/반환타입 제거 · **상수 리터럴은 보존** |
| 4. 위임 배제 | 정규화 본문이 **순수 파라미터 위임** 한 줄이면 후보에서 뺀다 (아래 경계표) |
| 5. 크기 | **정규화 후** AST 노드 수 ≥ `CLONE_MIN_AST_NODES` (= 14) |
| 6. 그룹핑 | `sha256(ast.unparse(정규화))[:12]` 가 동일한 함수를 한 그룹으로 묶고, 소속 **파일이 2개 이상**이면 교차파일 축(`duplicate_mechanical_helpers`), **한 파일 안**이면 동일파일 축(`duplicate_mechanical_helpers_local`)으로 나눈다 |

**3·5 단계가 같은 트리를 봐야 한다.** 원본에서 크기를 재고 정규화 후에 지문을 내면
기준이 어긋나 얇은 델리게이터가 오탐이 된다(§4.1). 구현은 정규화 트리 하나에서
`(digest, node_count)` 를 함께 낸다.

**4단계 — 순수 파라미터 위임 배제.** 이 형태가 곧 §4.1 처방("파라미터화된 해석기 +
얇은 명명 래퍼")의 산출물이므로, 계수하면 게이트가 자기 법을 처벌한다. 배제는 정규화
본문이 정확히 하나의 `ast.Return` 이고 그 값이 `ast.Call` 이며 다음을 **전부** 만족할
때만이다.

1. 호출 대상(`.func`)이 bare `ast.Name` (`ast.Attribute`·`ast.Subscript` 는 해당 없음)
2. 모든 위치 인자가 `ast.Name` 또는 `ast.Constant`
3. 모든 키워드 인자의 **값**이 `ast.Name` 또는 `ast.Constant`
4. `*`/`**` 언패킹(`ast.Starred` · `keyword.arg is None`)이 없음

정의를 더 넓게 잡으면(예: "본문이 `return <Call>` 하나면 배제") 한 줄로 쓴 진짜 복붙
까지 죽는다. 측정으로 확인한 경계:

| 본문 | 판정 | 이유 |
|---|---|---|
| `return average(values, digits=4)` | **배제** | bare 이름 + 파라미터/상수 인자 |
| `return _cast_or_none(value, int)` | **배제** | 동일 |
| `return resolve_band(RULES, text=t, title=title_line(t))` | 유지 | 키워드 값이 중첩 `Call` |
| `return "".join(str(v or "").strip().lower().split())` | 유지 | 호출 대상이 `Attribute` |
| `return tuple(item.strip() for item in raw.split(","))` | 유지 | 인자가 `GeneratorExp` |
| `return " ".join(f"{k}={v}" for k, v in rows)` | 유지 | 호출 대상이 `Attribute` |
| `return combine(*items, mode)` / `return combine(mode, **options)` | 유지 | 언패킹은 전달이 아니라 재조립 |

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
CLONE_MIN_AST_NODES = 14
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

`FileMetrics` 에 `duplicate_mechanical_helpers: int = 0` 과
`duplicate_mechanical_helpers_local: int = 0` 을 추가한다. 후자는 교차 파일 정보가
필요 없으므로 `scan_source` 안에서 pass 1 에 계산할 수 있고, 전자만 pass 2 에서
채운다. baseline JSON 은
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
| **새 허브에 canonical 을 두고 복사본을 남김** | 첫 행과 같은 경로다. 새 허브 파일이 baseline 에 없어 **0→1 로 위반** |

마지막 행은 통합 작업 자체가 걸리는 경로라서 따로 적는다. `_write_json`×5 를
`app/utils/jsonio.py` 로 옮기되 복사본을 지우지 않은 상태를 시뮬레이션한 실측:

```
app/utils/jsonio.py:              baseline 0 -> current 1   <-- VIOLATION
scripts/build_g2_exit_review.py:  baseline 2 -> current 2
```

따라서 **클론 그룹은 한 PR 안에서 원자적으로 통합한다.** 허브 신설과 복사본 제거가 같은
커밋에 있으면 그룹의 모든 파일이 동시에 내려가 통과한다. 나눠야 한다면 중간 커밋에서
`--update-baseline` 으로 허브의 위반을 잠그지 말고 PR 단위로 묶는다 — 잠그면 그 허브에
복붙 1건이 영구히 무료로 허용된다.

부분 통합 행은 수용 가능한 성질이지만 **부분 개선이 baseline 에 반영되지 않는다**는 점을
§8 에 한계로 명시한다.

### 4.5 튜닝 표면 고정

배제 목록·함수 열거 범위·**크기를 재는 기준**은 지표값을 직접 좌우하는 **튜닝 표면**
이다. 임계값 14 에서 후보 함수는 1,228 개이고, 이 집합의 경계를 정하는 것이 곧 이
표면이다. 민감도는 두 번 실증됐다.

- 열거 범위를 모듈 최상위 함수로 한정한 선택 하나 때문에 최초 측정이 클론을 25그룹으로
  과소 보고했다(§10.1).
- 크기를 **원본** AST 에서 재면서 지문은 **정규화 후**에 계산한 기준 불일치가 오탐
  6건(`_average`×6)을 baseline 에 동결시켰다. 처방의 산출물을 위반으로 계수하는
  상태였고, 픽스처가 그 케이스를 덮지 않아 테스트를 통과했다(경위는 §7.4).

그러므로 CLAUDE.md §4.5-3(특수케이스는 데이터로 선언)에 따라 고정한다.

- 배제 목록·임계값·열거 범위는 스캐너 상단 **선언 상수** (기존 7개 지표와 동일 스타일)
- `CLONE_ALLOWLIST` 상수로 의도적 예외를 사유와 함께 선언 (기존 `JSON_CALL_ALLOWLIST`
  패턴 재사용)
- **라벨링된 픽스처**로 predicate 를 양방향 고정 (§7.1)

`CLONE_ALLOWLIST` 의 키는 **콘텐츠 해시가 아니라 멤버 신원**(정렬된
`파일경로:함수명` 집합)으로 잡는다. 해시로 키를 잡으면 면제된 함수를 사소하게 고칠
때마다 해시가 바뀌어 빌드가 깨진다. 멤버 신원으로 잡으면:

- 본문을 고쳐도 여전히 클론이면 면제 유지 (멤버 불변)
- 세 번째 복사본이 생기면 멤버 집합이 달라져 **면제가 적용되지 않고 위반**
- 더 이상 클론이 아니게 되면 죽은 항목 검사(§7.3)가 실패시켜 제거를 강제

## 5. 통합 작업

### 5.1 목적지 구조

모듈에 적은 함수는 **지표가 실제로 잡은 통합 대상**(§5.2)뿐이다. 허브는 앞으로 생길
헬퍼의 주소이기도 하지만, 이 설계의 작업 범위는 측정된 것에 한정한다.

```
app/utils/                       app/ · scripts/ 공통 메커니컬 허브
  numeric.py                     coerce_float · coerce_amount · optional_float · optional_int
                                 average · rate · delta
  jsonio.py                      read_json_object · write_json · parse_json_or_text
                                 mapping_or_empty
  textfmt.py                     clip_title · normalize_category · clean_optional
                                 append_note
  sequence_coercion.py           (기존) + as_str_list

app/services/classification/
  _grouping.py                   memberships_by_group · tech_fields_by_group · format_groups

app/api/
  operator_common.py             with_current_operator

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

### 5.2 통합 매핑 (측정된 36그룹)

그룹은 **함수명으로 식별**한다. 구 측정의 `G01`~`G25` 번호는 스캔 범위가 달라 현재
그룹과 대응하지 않으므로 쓰지 않는다.

| PR | 대상 | 목적지 |
|---|---|---|
| PR2 | `optional_float`(`app/ai/guardrail_core.py`) · `_safe_optional_int`(`app/services/synthetic_experiment/sample_gap.py`) | `app/utils/numeric.py` |
| PR2 | `_coerce_amount`(`app/ai/predictors/legal_floor_spec.py`) · `_coerce_float`(`app/services/award_verification.py`) · `_as_float`(`app/services/decision_samples.py`) · `amount_float`(`scripts/backtest_latest_award_holdouts.py`) — **네 번째 멤버가 `scripts/` 다. PR2 가 그 스크립트까지 같은 커밋에서 처리한다**(§5.1 대로 스크립트가 `app.utils.numeric` 을 직접 import) | `app/utils/numeric.py` |
| PR2 | `_rate`×2 (`app/services/analytics_reporting/base.py` · `prediction_reporting.py`) | `app/utils/numeric.py` |
| PR2 | `_delta`×2 (`app/services/decision_analytics/base.py` · `decision_experiments/base.py`) | `app/utils/numeric.py` |
| PR2 | `_memberships_by_group` / `_tech_fields_by_group` (`app/services/classification/`) | `app/services/classification/_grouping.py` |
| PR2 | `_format_groups`×2 (`app/services/classification/`) | `app/services/classification/_grouping.py` |
| PR2 | `_as_str_list` (`app/schemas/opportunity.py` · `app/services/bid_summary.py`) | `app/utils/sequence_coercion.py` |
| PR2 | `_normalize_category` (`app/services/backtest_cutoff.py` · `prediction_dataset.py`) | `app/utils/textfmt.py` |
| PR2 | `_clean_category_name`(`app/services/decision_experiments/application.py`) · `_clean_optional`(`app/services/ml_training/helpers.py`) | `app/utils/textfmt.py` |
| PR2 | `normalize_agency_name`×2 (`app/ai/predictors/historical/statistics.py` · `app/services/koneps/parsing.py`) | `koneps/parsing.py` canonical → `statistics.py` 가 import |
| PR2 | `_append_reasoning_note`(`app/services/allocation.py`) · `_append_note`(`app/services/real_bid_track.py`) | `app/utils/textfmt.py` |
| PR2 | `_rollout_check`(`app/services/ml_release/base.py`) · `_check`(`app/services/ml_release/storage/base.py`) | `app/services/ml_release/` 내부 공통화 |
| PR2 | `_with_current_operator`×2 (`app/api/analytics.py` · `app/api/decision_samples.py`) | `app/api/operator_common.py` |
| PR2 | `guidance_for`(`app/services/smoke_failure_taxonomy.py`) · `failure_guidance`(`scripts/production_smoke_test.py`) | `smoke_failure_taxonomy.py` canonical → 스크립트가 import |
| PR2 | `_email_for`(`app/services/synthetic_custom_operator.py`) · `_email`(`scripts/seed_synthetic_operators.py`) | `synthetic_custom_operator.py` canonical → 스크립트가 import |
| PR2 | **동일파일 5그룹** — §4.1 표의 5쌍 | 각 파일 안에서 파라미터화 + 얇은 명명 래퍼 유지 (§4.1 처방) |
| PR3 | `_read_json_object`×3 (`build_g2_exit_review` · `g2_blocking_gap_register` · `verify_g2_notification_targets`) | `app/utils/jsonio.py` |
| PR3 | `_write_json`×4 + `write_json`×1 (`build_g2_exit_review` · `check_g2_exit_readiness` · `collect_g2_evidence` · `g2_blocking_gap_register` · `verify_g2_notification_targets`) | `app/utils/jsonio.py` |
| PR3 | `parse_json_or_text`×2 (`collect_g2_evidence` · `production_smoke_test`) | `app/utils/jsonio.py` |
| PR3 | `_mapping_or_empty`(`app/ai/predictors/artifact_contracts.py`) · `_as_dict`(`scripts/check_g2_exit_readiness.py`) | `app/utils/jsonio.py` |
| PR3 | `_optional_int`×2 (`collect_g2_evidence` · `verify_g2_notification_targets`) | `app/utils/numeric.py` |
| PR3 | `parse_thresholds`×2 (`backtest_latest_award_holdouts` · `report_eligibility_segment_backtest`) | `scripts/_common/cliargs.py` |
| PR3 | `parse_actions`×2 (`backtest_paper_bidding` · `backtest_synthetic_operators`) | `scripts/_common/cliargs.py` |
| PR3 | `parse_csv`×2 (`backtest_latest_award_holdouts` · `report_eligibility_segment_backtest`) | `scripts/_common/cliargs.py` |
| PR3 | `_count_lines`×3 (`report_eligibility_labels` · `report_license_eligibility` · `report_license_gate_impact`) | `scripts/_common/report.py` |
| PR3 | `_clip_title`×2 (`report_license_eligibility` · `report_license_gate_impact`) | `app/utils/textfmt.py` |
| PR3 | `_verdict_line`×2 (`report_license_eligibility` · `report_license_gate_impact`) | `scripts/_common/report.py` |
| PR3 | `_ordered_segments`×2 (`report_eligibility_segment_backtest` · `report_no_candidate_cause`) | `scripts/_common/report.py` |
| PR3 | `_classify_label_for` · `_segment_label_for` (`report_eligibility_segment_backtest` · `report_no_candidate_cause`) — 이름이 달라 의미 동일성 확인 후 통합 | `scripts/_common/report.py` |
| PR3 | `format_threshold` · `_format_threshold` (`backtest_latest_award_holdouts` · `report_eligibility_segment_backtest`) | `scripts/_common/report.py` |

합계: PR2 20그룹(교차 15 + 동일 5) · PR3 14그룹 · 보류 2그룹 = **측정된 36그룹 전부**를
다룬다 (교차파일 31 + 동일파일 5).

`_classify_label_for` / `_segment_label_for` 은 이름이 다르므로 통합 전에 두 함수의
**의미가 실제로 같은지** 확인한다. 다르면 allowlist 로 옮긴다.

**각 행은 그룹 전체를 한 커밋에서 처리한다.** §4.4 마지막 행대로, 새 허브
(`app/utils/numeric.py` 등)에 canonical 만 두고 복사본을 남기면 그 허브 파일이 0→1 로
위반이 되어 PR 이 첫 커밋부터 실패한다. 허브 신설 · 복사본 제거 · 호출부 이전이 같은
커밋에 있어야 그룹의 모든 파일이 동시에 내려간다.

### 5.2.1 지표 임계값 아래의 중복

지표가 잡지 않는 중복이 따로 있다. 원인은 셋이고 **임계값만이 아니다**(실측 확인).

| 중복 | 미탐 원인 |
|---|---|
| `_csv_safe`×2 (`app/services/decision_samples.py` · `app/services/bid_form_draft.py`) | 둘 다 `@staticmethod` 라 §4.2 1단계에서 후보에조차 오르지 않는다. 사실상 동일한 본문이다 |
| `_round_optional`×2 (`app/services/prediction_feedback.py` 메서드 25노드 · `app/services/dashboard_summary/normalizers.py` 모듈 함수 24노드) | 둘 다 임계값 위지만 한쪽이 메서드라 `self` 가 인자 하나로 남아 정규화 지문이 갈린다 |
| `_kst_stamp`×4 (`scripts/` 4파일, 10노드) · `kst_now`/`utc_now`(`app/core/time.py`, 10노드) | 정규화 14노드 미만 |

해당 파일을 어차피 건드리는 PR 에서는 같이 정리하되, §7.4 의 성공 기준에는 넣지 않는다.
지표가 세지 않는 것을 목표로 잡으면 검증할 수 없기 때문이다. `kst_now`/`utc_now` 는
의도적으로 대칭인 쌍이라 통합 대상이 아니다 — 임계값이 이를 자동으로 걸러 준다.

### 5.3 통합하지 않고 보류하는 2건

교차파일 31그룹 전부가 통합이 옳은 것은 아니다. 다음 둘은 `CLONE_ALLOWLIST` 에 **사유와 함께
등재**하고 코드를 건드리지 않는다. 둘 다 현재 main 에도 그대로 존재함이 재확인됐다
(digest 일치).

- `app/services/award_verification.py:158 _rate_to_fraction` /
  `app/services/base_amount_basis.py:64 normalize_winning_rate`.
  구조는 같지만 **금액 basis 도메인**이다. 이 저장소에서 basis 혼동은 반복 버그의
  근본원인으로 지목된 영역이므로, 두 함수의 basis 의미가 동일함을 증명하기 전에 합치면
  버그를 만든다.
- `app/ai/predictors/registry.py:13 build_default_predictor_registry` /
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
- 교차 파일 중복은 `duplicate_mechanical_helpers` 가, 동일 파일 중복은
  `duplicate_mechanical_helpers_local` 이 **자동 차단**한다. 두 지표 중 하나라도 오르는
  PR 은 CI 에서 실패한다
- 같은 알고리즘이 상수·캐스트만 달라 반복되면 **파라미터화된 해석기 + 얇은 명명 래퍼**로
  쓴다. 이름의 가독성은 래퍼가 지키고, 알고리즘은 한 벌만 존재한다
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
- 동일 파일 내 상수만 다른 쌍 (`extract_eligibility_flags` /
  `_project_license_limit_item` — `…_local` 지표)
- 동일 파일 내 캐스트만 다른 쌍 (`_int_or_none` / `_float_or_none` — `…_local` 지표).
  **합성 시그니처가 아니라 실제 소스 문자열로** digest 동치를 고정한다 — 합성
  `CloneSignature` 로만 덮으면 정규화 경로가 바뀌어도 픽스처가 깨지지 않는다
- **한 줄로 쓴 진짜 복붙** — `return "".join(…)`(호출 대상이 `Attribute`) ·
  `return tuple(x.strip() for x in …)`(인자가 `GeneratorExp`) ·
  `return f(RULES, title=g(t))`(키워드 값이 중첩 `Call`) · `return f(*items, mode)`.
  §4.2 4단계 배제가 **너무 넓어지지 않았음**을 고정한다

**반드시 놓쳐야 할 것**

- 데코레이터가 붙은 얼짜 FastAPI 라우터 (`app/api/operator.py` 패턴)
- DB 세션에 접근하는 서비스 메서드
- 상수 리터럴만 다른 쌍 (`round(x, 2)` vs `round(x, 3)`)
- **파라미터화 후 남는 얇은 명명 델리게이터**. 이 케이스가 깨지면 §4.1 의 "이름은
  지키고 중복만 없앤다" 처방이 성립하지 않는다. 두 형태를 모두 고정한다 —
  (a) annotation 없는 짧은 델리게이터(정규화 10~12노드, 임계값 아래),
  (b) **타입힌트 + docstring 이 붙어 원본 노드 수가 임계값을 넘는 델리게이터**
  (`_average` 실사례: 원본 29노드 · 정규화 12노드). (b) 가 없으면 크기 기준이 원본으로
  퇴행해도 테스트가 통과한다 — 실제로 그렇게 회귀했다(§7.4)
- **annotation·docstring 유무가 노드 수를 바꾸지 않음** — 같은 알고리즘에 타입힌트만
  붙인 쌍이 같은 `node_count` · 같은 digest 를 갖는지 고정한다
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

측정된 교차파일 인벤토리는 31그룹 / 69함수다. 이 중 allowlist 2그룹(4함수, §5.3)은
지표에서 제외되므로, 래칫이 기록하는 값은 아래와 같다. **현재·PR1 행은 PR1 이
`tests/design_ratchet_baseline.json` 에 실제로 동결한 값이다.**

| 시점 | 교차파일 지표 (`duplicate_mechanical_helpers`) | 동일파일 지표 (`…_local`) |
|---|---|---|
| 현재 | 29 그룹 / **65 함수** | 5 그룹 / **10 함수** |
| PR1 완료 | 동일 (baseline 동결 — 이 시점부터 증가 차단) | 동일 |
| PR2 완료 | 14 그룹 / 33 함수 | **0 그룹 / 0 함수** |
| PR3 완료 | **0 그룹 / 0 함수** | 0 그룹 / 0 함수 |

PR2 이후 값은 **통과 조건이 아니라 기대치**다. PR2·PR3 이 §5.2 의 배분대로 진행된다는
가정에서 나온 산술이다(PR2 가 교차 15그룹 / 32함수, PR3 이 나머지 14그룹 / 33함수).

**PR1 의 baseline 은 한 번 재동결됐다.** 최초 동결값은 교차 67함수였는데, 그중 6건이
오탐이었다 — 크기를 원본 AST 에서 재는 바람에 이미 공용 `average()` 로 통합을 마친
`_average`×6 이 계수됐다. 처방의 산출물이 위반으로 잡히는 상태였으므로 PR3 의 "0/0"
목표가 애초에 도달 불가능했다. 크기 기준을 정규화 후로 바꾸고 순수 파라미터 위임을
배제한 뒤(§4.1·§4.2) 재동결한 값이 위 표다. 같은 변경으로 임계값이 20→14 로 내려가
실제 중복 2그룹(`_email_for`×2 · `format_threshold`×2, 각 15·14노드)이 새로 잡혔고,
기존 7개 지표는 파일×지표 전수 비교에서 증가 0 이었다.

동일파일 목표가 0 인 것은 §4.1 에서 5그룹 전수를 확인한 결과 전부 환원 가능하다고
판정했기 때문이다. 파라미터화 후 남는 얇은 명명 래퍼는 §4.2 4단계가 배제하고, 그것을
빠져나가더라도 정규화 10~12노드라 임계값(14) 아래다. 이 처방의 산출물이 실제로 0 으로
떨어지는지는 §7.1 픽스처가 고정한다.

프론트 목표치는 PR4 스캐너 리포트가 확정한다. 현재 프론트 수치는 수동 grep 추정이므로
목표로 박지 않는다.

## 8. 범위 밖 · 알려진 한계

이 설계가 **잡지 못하는 것**을 명시한다. 지표는 총량 측정이 아니라 증가 차단 래칫이다.

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
2. `ast.walk(tree)` 로 **모든** 함수/비동기함수 노드를 순회한다 — 모듈 최상위뿐 아니라
   **메서드와 중첩 함수를 포함**한다. 최상위만 세면 헬퍼를 클래스로 감싸는 것만으로
   지표를 회피할 수 있다
3. §4.2 의 predicate 로 메커니컬 여부 판정
4. `ast.unparse` → `ast.parse` 로 복제한 뒤 `name="f"` · `decorator_list=[]` ·
   `returns=None`, 첫 문장이 문자열 상수 `Expr`(docstring)이면 제거
5. docstring 제거 후 본문이 **순수 파라미터 위임**(§4.2 4단계)이면 제외
6. `NodeTransformer` 로 `ast.Name.id` 와 `ast.arg.arg` 를 등장 순서대로 `v0,v1,…` 치환,
   `arg.annotation=None`
7. **정규화가 끝난 트리**의 AST 노드 수를 세고 `CLONE_MIN_AST_NODES`(14) 미만은 제외.
   크기와 지문을 같은 트리에서 재야 기준이 어긋나지 않는다(§4.1)
8. `sha256(ast.unparse(정규화))[:12]` 를 그룹 키로 사용
9. 멤버가 2개 이상인 그룹만 남기고, 소속 파일이 2개 이상이면 교차파일
   축(`duplicate_mechanical_helpers`), 한 파일 안이면 동일파일
   축(`duplicate_mechanical_helpers_local`)으로 나눈다

PR1 이후에는 `python scripts/design_ratchet.py` 가 이 절차를 수행하고 리포트를 낸다.

### 10.1 최초 측정의 정정

이 문서의 최초 측정은 2번 항목을 **모듈 최상위 함수만** 순회하도록 구현해 클론 그룹을
25그룹으로 과소 보고했다. `_average` 처럼 클래스 메서드로 존재하는 중복이 통째로
빠졌다. 스크립트를 `ast.walk` 기준으로 고쳐 최초 측정 시점 커밋(`db57f8c`)에 되돌려
돌린 결과 35그룹이었고, 현재 main(`a2b90ca`)은 36그룹이다. 본문의 수치는 이
apples-to-apples 재측정으로 정정한 값이다.

메서드·중첩 함수를 제외하는 선택은 **채택하지 않는다.** 제외하면 헬퍼를 클래스로 감싸는
것만으로 래칫을 회피할 수 있는 구멍이 열리기 때문이다.
