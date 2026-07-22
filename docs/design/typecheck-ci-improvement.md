# Typecheck + CI 개선 이니셔티브

반복적으로 재발한 `base`/`basis` 계열 버그(예: #199 base 오염, #195/#200 밴드·floor 정합)의
근본 원인 중 하나는 **정적 타입 검증과 CI 게이트의 부재**다. 이 문서는 그 공백을 메우는
다단계 이니셔티브의 목표·단계·규율을 기록한다. Phase 0는 이 문서와 함께 착수한다.

## 목표 (4)

1. **회귀 조기 차단.** 머지 전에 lint + 타입체크 + 유닛 테스트가 자동으로 도는 CI를 둔다.
2. **점진적 타입 안전.** 레거시를 한 번에 다시 쓰지 않고, **신규/핵심 모듈부터** strict 타입으로
   끌어올리는 ratchet 경로를 만든다.
3. **도구 드리프트 제거.** 설치된 도구(ruff)와 실제 사용 도구(flake8/black)의 불일치를 정리하고,
   단일 lint/typecheck 진입점을 만든다.
4. **과설계 금지.** 전역 `Money`/`Basis` 값 타입 재작성 같은 대규모 리팩터로 번지지 않게,
   범위를 "버그가 실제로 났던 좁은 도메인(금액·사정률 변환)"으로 한정한다.

## 비목표 (명시적 배제)

- 레거시 147파일의 타입 주석 일괄 추가/재작성.
- 전역 `Money` 래퍼 타입 도입 또는 SQLAlchemy 모델의 `Mapped[...]` 전면 전환.
- 코드 대량 리포맷(E501 일괄 개행 등)이나 동작 변경.

## 단계

### Phase 0 — 파운데이션 (본 PR, 동작 변화 0)

- `pyproject.toml` 신설: mypy(전역 lenient) + ruff(config) 선언.
- `requirements/dev.txt`에 `mypy` 핀 추가.
- `.github/workflows/ci.yml` 신설: `ruff check app/` → `mypy app/` → `pytest -q tests/`.
- `Makefile`: `lint`을 ruff로 전환, `typecheck` 타깃 추가.
- **수용 기준: 앱 로직 코드 변경 0.** config·CI·docs·Makefile·requirements만 변경한다.

현재 코드 실측(Phase 0 착수 시점):

| 도구 | 전역 기본값 결과 | 처리 |
|---|---|---|
| ruff (기본 select `E4,E7,E9,F`) | 5건 | `per-file-ignores`로 grandfather (아래) |
| ruff E501 @ 88 | 1,793건 | **select에서 제외**(대량 리포맷 금지). line-length=88만 선언 |
| mypy (lenient) | 686건 / 69파일 | 대부분 SQLAlchemy `Column[X]` 오탐 → `app.*` `ignore_errors`로 grandfather |

#### ruff grandfather 목록 (`[tool.ruff.lint.per-file-ignores]`)

한 파일당 정확히 현재 위반 코드만 억제한다. 코드를 고칠 때 해당 항목을 삭제한다.

- `app/ai/bid_recommendation.py` `F841` — 미사용 지역변수 `user_avg_bid` (ml 소유 파일).
- `app/api/admin.py` `F401` — 미사용 import `models.User`.
- `app/api/analytics.py` `F401` — 미사용 import `models.BidDecisionRecord`.
- `app/services/allocation.py` `F821` — **오탐**: `"BidDecisionSaveRequest"`는 따옴표 forward-ref이고
  실제 이름은 메서드 본문(`allocation.py` 내부)에서 지연 import된다. 런타임 정상.
- `app/services/project_similarity.py` `E741` — 모호한 한 글자 변수 `l`.

> F401 2건과 F841 1건은 명백한 사소 정리 대상이지만, Phase 0는 **config-only**라 코드 수정 대신
> grandfather로 격리했다. 후속 cleanup PR에서 제거한다.

### Phase 1 — 도메인 island를 strict로 (금액·사정률 변환)

버그가 실제로 났던 좁은 도메인을 **작은 신규 모듈**로 추출하고 그 모듈만 strict 타입으로 올린다.
예상 후보: `app/domain/money.py`, `app/domain/basis_conversion.py`(예정가↔사업금액/기초금액,
사정률 변환). 이 모듈들은 순수 함수 + 명시 타입으로 작성하고, 아래 island override를 켠다.

### Phase 2 — ratchet 확장

island을 하나씩 늘린다(예측 순수 코어 → 게이트/floor 순수 함수 → 스키마 경계). 각 승격은
"그 모듈을 실제로 타이핑"한 뒤에만 override에 추가한다. 목표는 커버리지 숫자가 아니라
**버그 밀집 경로의 타입 안전**이다.

## mypy island ratchet 방법

전역 프로필은 lenient(strict 플래그 없음)이고, `app.*` 와일드카드가 `ignore_errors=true`로
레거시 전체를 grandfather한다. 신규 모듈을 strict로 올리려면 `pyproject.toml`에 **정확한 모듈명**
override를 추가한다:

```toml
[[tool.mypy.overrides]]
module = ["app.domain.money", "app.domain.basis_conversion"]
ignore_errors = false
disallow_untyped_defs = true
disallow_incomplete_defs = true
warn_return_any = true
no_implicit_optional = true
strict_equality = true
```

**정확한 모듈명은 `app.*` 와일드카드보다 우선한다**(mypy 규칙: 와일드카드 없는 섹션이 더 구체적이라
순서와 무관하게 이김). 따라서 island만 완전 검증되고 나머지 `app.*`는 grandfather로 남는다.
이 동작은 Phase 0에서 임시 override(`app.api.analytics`)로 검증했다: 해당 모듈만 24건 재노출,
나머지는 green 유지.

**주의:** 타이핑하지 않은 레거시 모듈을 island에 넣지 말 것 — 억눌린 ~686 오탐이 되살아난다.
반드시 "모듈을 먼저 타이핑 → override 추가" 순서를 지킨다.

## CI 구성 요약

`.github/workflows/ci.yml` (push/PR 트리거):

1. Python 3.12 셋업 + pip 캐시.
2. `pip install -r requirements/runtime.txt -r requirements/dev.txt`.
   유닛 테스트는 SQLite 격리(`tests/conftest.py`)라 외부 서비스/시크릿 불요이고, ML 스택
   (torch/sklearn/pandas)을 import 시점에 건드리지 않으므로 무거운 ml-embedding/ml-training
   휠은 설치하지 않는다(`sentence_transformers`는 `classifier.py`에서 지연 import).
3. `ruff check app/` → `mypy app/` → `pytest -q tests/`.

## 로컬 명령

```bash
make lint        # ruff check app/
make typecheck   # mypy app/
make test        # pytest -v --cov=app
```

## 신규 KONEPS 수집 필드 추가 절차 (Phase 2c 실배치 검증 게이트)

반복 버그(#209 자격상세 목록 부재, #210 차수 int 파괴, #220 success_rate=예정가)는 전부
**신규 수집 필드를 소비 전에 실배치 검증하지 않아** 라이브에서 터졌다("기능추가→라이브실측→
사후수정"). 재발을 막기 위해, KONEPS 응답에서 **새 필드를 소비 코드에 넣기 전에** 아래
절차를 따른다.

1. **소비 전 실배치 검증.** 소량의 실 KONEPS 응답으로 그 필드가 정말 기대한 의미/타입/존재
   인지 assert 한다(읽기 전용, throttle, resultCode 게이트, 시크릿 미출력):

   ```bash
   # 계약만 출력 (호출 없음)
   docker exec bid_vector_api python scripts/verify_koneps_field_contract.py --dry-run
   # 공고 목록 5건 실배치 검증
   docker exec bid_vector_api python scripts/verify_koneps_field_contract.py \
       --scope notice --category service --limit 5
   # 개찰/낙찰 목록 (success_rate/차수 계약)
   docker exec bid_vector_api python scripts/verify_koneps_field_contract.py \
       --scope scsbid --category construction --limit 5
   ```

   리포트의 **미지(unknown) 필드** 목록에 그 신규 필드가 뜬다 — 소비 전에 사람이 검토하라는
   신호다. 값의 스케일/타입/범위를 실 응답으로 눈으로 확인한다.

2. **계약을 데이터로 등록.** 함정 소지가 있는 필드(율/식별자/금액 basis/계열 제한)는
   `app/services/koneps/field_contract.py` 의 선언 테이블에 등록한다(§4.5.3 규칙=데이터):
   - 율/식별자 트랩 → `FIELD_CONTRACTS` 에 `FieldContract` 한 줄(raw 이름·개념·basis·스케일·
     제로패딩·예상 범위·`present_in` 계열·실측 출처 주석).
   - 금액 base 후보 키 → `BASE_RESOLUTION_ORDER` / `_KEY_BASIS` / `_TRUE_BASE_KEYS`.
   - 소비 코드가 다루기 시작한 키 → `KNOWN_FIELDS` (미지 목록에서 빠지도록).
   - 규칙은 데이터로만 추가하고, 코드는 순수 검증기(해석기)만 유지한다. 각 계약에 위반/정상
     값-테이블 유닛 테스트를 붙인다(`tests/test_koneps_field_contract.py`).

3. **함정 기준선(실측 확정).** 계약이 인코딩하는 확정 사실:
   - `sucsfbidRate`(success_rate) = 낙찰가/**예정가**(사정률), 기초금액 아님. 범위 ~0.5~1.0.
   - `bidNtceOrd` = 차수, **제로패딩 문자열**("000"). int 변환 시 "000"→0 으로 KONEPS 빈 응답.
   - `base_amount` 후보 키에 예산/예정가/기초금액이 뒤섞임 — 예정가/예산이 기초금액보다 먼저
     선택되면 base==예정가 오염.
   - `sucsfbidLwltRate`(낙찰하한율) = 광범위 목록엔 없고 표적조회(inqryDiv=2)/서브op만.
   - KONEPS 는 잘못된 파라미터·쿼터에 **HTTP 200 + 에러 resultCode** 를 준다(`check_result_code`
     게이트가 검증).

`field_contract` 는 mypy strict 아일랜드다(과설계 금지: `app.domain.money.Basis` 만 재사용하는
자립 순수 모듈). 스크립트는 그 순수 검증기를 실 응답에 태우는 얇은 IO 경계다.
