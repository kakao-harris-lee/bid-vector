# 설계 래칫 (design ratchet)

- 데이터 계약 · 순수 비교: `scripts/_design_ratchet_contracts.py`
- 측정 코어(AST 스캔): `scripts/_design_ratchet_scan.py`
- CLI · baseline 영속화 · 리포트: `scripts/design_ratchet.py`
- baseline: `tests/design_ratchet_baseline.json`
- 게이트: `tests/test_design_ratchet.py`

## 목적

이 저장소에는 이미 긴 함수, `json` 직접 호출, `dict` 경계가 많다. 한 번에 고칠 수 없고,
그렇다고 방치하면 계속 늘어난다. 그래서 **현재값을 baseline 으로 고정**하고 pytest 가
**증가만** 막는다. 감소·파일 삭제는 항상 통과한다(래칫 = 한 방향 톱니).

지표는 **파일별**로 센다. 전역 합계로 재면 한 파일의 개선이 다른 파일의 악화를 가려서
래칫이 새기 때문이다.

파싱·디코딩할 수 없는 파일은 건너뛰지 않고 `RatchetScanError` 로 크게 실패한다. 조용히
넘기면 그 파일의 위반이 0 으로 보고되어 "삭제 = 통과" 경로로 사라진다.

## 지표

| 지표 | 정의 | 왜 |
|---|---|---|
| `functions_over_soft_limit` | 51줄 이상 함수 수 | CLAUDE.md §4.5-4 함수 ~50줄 |
| `functions_over_hard_limit` | 101줄 이상 함수 수 | 분해가 시급한 함수 |
| `file_loc_band` | **500줄 초과** 파일의 25줄 밴드 `ceil(LOC/25)` (500줄 이하면 항목 없음) | §4.5-4 파일 ~500줄. LOC 원값이 아니라 밴드로 재서, 큰 파일에 한 줄 더하는 버그픽스가 baseline 상승을 요구하지 않게 한다(501~525줄 → 21, 526~550줄 → 22) |
| `json_direct_calls` | `json.loads/dumps/load/dump` 호출 지점 수 | 경계를 원시 dict 로 흘리는 주 원인 |
| `dict_boundary_functions` | 인자(`*args`/`**kwargs` 포함)나 반환 annotation 이 약한 경계인 함수 수 | 검증되지 않는 경계 |
| `env_test_sniff` | 환경값 읽기와 `"test"` 리터럴이 같은 Compare 노드에 있는 지점 수(`app/` 만) | 프로덕션 코드가 테스트를 스니핑하는 지점 |
| `unvalidated_dict_tasks` | celery task 중 검증되지 않는 입력을 받으면서 **그 입력을 대상으로 한** `model_validate`/`Model(**payload)` 승격이 본문에 없는 함수 수 | 재배달 payload 가 검증 없이 흐르는 지점 |
| `duplicate_mechanical_helpers` | 그 파일이 정의한 메커니컬 헬퍼 중 **다른 파일**의 헬퍼와 구조 클론 그룹을 이루는 함수 수 | §4.5-8 중복 금지. 처방은 허브로 이동 |
| `duplicate_mechanical_helpers_local` | 그 파일 **안에서만** 서로 구조 클론인 메커니컬 헬퍼 수 | §4.5-8. 처방은 파라미터화된 해석기 + 얇은 명명 래퍼 |

### 약한 경계(weak annotation) 판정

- bare `dict` / `Dict` / `Any` / `Mapping` / `MutableMapping` / `object` → 약함
  (`object` 는 `Any` 보다 약한 계약이라 같은 부류로 센다 — 무비용 우회 차단).
- `X | None` · `Optional[X]` · `Union[...]` 은 래퍼를 벗겨 멤버 단위로 판정한다
  (`dict[str, Any] | None` → 약함).
- 첨자가 붙은 매핑은 **value 가 약한 타입일 때만** 약하다. value 가 `dict`/`Any`/
  `object` 이거나 **중첩적으로 약한 컨테이너**면 카운트하고(`dict[str, Any]`,
  `dict[str, object]`, `dict[str, dict[str, object]]`,
  `dict[str, list[dict[str, Any]]]`), value 가 구체 모델·스칼라면 **면제**한다
  (`dict[str, ConcreteModel]`, `dict[str, str]`, `Mapping[str, int]`).
- 컨테이너(`list`/`set`/`tuple`/`Sequence`/`Iterable` …)는 그 자체로 약하지 않지만
  **안에 든 것이 약하면** 약하다: `list[dict[str, Any]]`·`list[dict]` 는 카운트하고
  `list[int]`·`list[ConcreteModel]`·`tuple[int, str]` 은 면제한다. 컨테이너로 한 겹
  감싸서 지표를 피하는 우회를 막는 규칙이다.

### `unvalidated_dict_tasks` 의 입력 판정

`@celery_app.task` / `@shared_task` 가 붙은 함수에서 `self`/`cls`(celery `bind=True` 수신자
포함)를 제외한 인자 중 **약한 경계이거나 어노테이션이 아예 없는** 인자가 있으면 검증되지
않는 입력으로 본다. 어노테이션을 지우거나 `**kwargs: Any` 로 받는 것으로 지표를 피할 수
없다.

승격(면제) 판정은 **대상 이름**까지 본다. `Model.model_validate(x)` 의 **위치 인자** 또는
`Model(**x)` 의 splat 대상이 **그 task 의 weak 파라미터 이름을 참조**할 때만 면제한다
(`request_payload or {}`, `{"notices": notices or []}` 처럼 감싸도 안쪽 참조를 찾고,
`schemas.Req(**payload)` 같은 모듈 경로 호출은 마지막 세그먼트로 판정한다). 따라서 payload
와 무관한 `Other.model_validate(CONST)` 나 `Thread(**options)` 로는 면제되지 않고,
`Other.model_validate(CONST, context=payload)` 처럼 payload 를 **keyword 인자로만** 스치는
참조 세탁도 면제 근거가 아니다. 또 승격 탐색은 **중첩 함수·lambda 본문을 제외**한다 —
호출되는지 알 수 없는 정의 안의 승격은 payload 검증 증거가 못 된다.

이 판정의 잔여 오차(알고도 두는 것):

- **미탐**: splat 대상이 실제 payload 인 비-DTO 호출(`Thread(**payload)`)은 이름만으로
  DTO 와 구분할 수 없어 여전히 면제된다.
- **오탐(FP)**: 승격 대상이 payload 이름을 직접 들고 있지 않은 형태는 실제로 검증하더라도
  카운트된다 — comprehension 변수 경유(`[Req.model_validate(p) for p in payload]`), 1단
  지역 재바인딩(`data = payload or {}` 후 `Req.model_validate(data)`), keyword 형태
  (`Req.model_validate(obj=payload)`). 이름 추적(별칭 해석)을 하지 않는 대가이며, 실제
  task 는 모두 직접 참조 형태라 현재 저장소 카운트는 0 이다. 걸리면 승격을 payload 를
  직접 참조하는 한 줄로 바꾸는 것이 정답이다.

### `env_test_sniff` 의 탐지 범위(한정)

같은 `Compare` 노드 안에 **환경값 읽기**와 **`"test"` 리터럴**이 함께 있으면 센다.

- 환경값 읽기: `X.ENVIRONMENT` 속성 접근, `os.environ["ENVIRONMENT"]`,
  `os.getenv("ENVIRONMENT")` / `os.environ.get("ENVIRONMENT")`(키를 keyword 로 넘기는
  `os.getenv(key="ENVIRONMENT")` 포함).
- `"test"` 리터럴: 직접 비교(`== "test"`, `!= "test"`)뿐 아니라 **리터럴 컨테이너**의
  원소(`in ("test", "ci")`, `in {"test"}`, `not in ["test"]`)도 포함한다.

`settings.ENVIRONMENT in NON_DELIVERING_ENVIRONMENTS` 처럼 **선언 데이터(이름) 참조**
멤버십은 세지 않는다. 인라인 스니핑을 데이터 선언으로 바꾼 것이 이 저장소가 채택한
패턴이라(`app/core/constants.py`) 지표로 벌하면 안 된다. 리터럴 집합만 본다.

**이 면제는 원칙이 아니라 알려진 우회 통로(stated bypass)다.** 파일 안에 한 줄짜리 지역
상수(`_TEST_ENVS = {"test"}`)를 두고 `settings.ENVIRONMENT in _TEST_ENVS` 라고 쓰면 스니핑을
그대로 하면서 지표를 피할 수 있다. 이름 참조를 해석하지 않는 대가로 남겨 둔 구멍이므로,
리뷰에서 "선언 데이터로 바꿨다"는 주장은 그 상수가 `app/core/constants.py` 의 공유 선언인지
확인한다.

여전히 **미탐**인 형태: `env = settings.ENVIRONMENT` 후 변수 비교,
`str(settings.ENVIRONMENT).lower() == "test"` 처럼 읽기를 함수로 한 번 감싼 형태,
`{a, settings.ENVIRONMENT} & {"test"}` 같은 집합 연산(Compare 가 아님), 헬퍼 함수로 감싼
판정. 이 지표는 "테스트 스니핑 총량"이 아니라 **흔한 형태의 증가를 막는 래칫**이다.

### 중복 헬퍼 지표의 판정 파이프라인

측정 코어는 `scripts/_design_ratchet_clones.py` 다(스캐너 자신도 500줄 한도를 지켜야
해서 `_design_ratchet_scan.py` 에서 분리했다).

| 단계 | 규칙 |
|---|---|
| 1. 후보 | **데코레이터가 하나도 없는** 함수/메서드. 라우터·celery task·`property` 가 전부 배제된다 |
| 2. 메커니컬 | 본문에 `MECHANICAL_EXCLUDED_NAMES`(db·session·requests·settings·logger·self·cls …) / `MECHANICAL_EXCLUDED_ATTRS`(query·commit·execute …) 가 없고 `global`/`nonlocal` 이 없음 |
| 3. 크기 | AST 노드 수 ≥ `CLONE_MIN_AST_NODES`(20) |
| 4. 정규화 | 변수·인자명 → 등장순 `v0,v1,…` · docstring/데코레이터/annotation/반환타입 제거 · **상수 리터럴은 보존** |
| 5. 그룹핑 | 정규화 본문의 sha256 앞 12자가 동일 |

`open`·`Path`·`json`·`datetime` 은 **일부러 배제하지 않는다**. 얇은 메커니컬 I/O
래퍼(`_write_json` 류)가 실제 중복의 최대 밀도 구간이라 대상에 포함해야 한다.

상수 리터럴을 보존하므로 `round(x, 2)` 와 `round(x, 3)` 은 다른 그룹이다(보수적 판정).
반대로 annotation 을 제거하므로 타입만 다른 쌍은 같은 그룹이 된다(제네릭 통합 후보가
맞으므로 의도된 동작).

**미탐 범위(한정).** 이 지표도 총량 측정이 아니라 증가 차단 래칫이다.

- **의미적 유사** — 구조가 다르면서 같은 일을 하는 함수는 잡히지 않는다.
- **부분 통합** — 4곳 중 1곳만 제거하면 남은 3곳이 각 1을 유지해 지표가 줄지 않는다.
  위반은 아니지만(증가가 아니므로) 개선이 baseline 에 반영되지도 않는다.
- **클래스·타입·상수의 중복** — 함수만 스캔한다.
- **20 AST 노드 미만** — `_average` 류 소형 중복은 임계값 아래라 계수되지 않는다.

스캔 대상은 `app/`·`scripts/` 의 `*.py`(`__pycache__` 제외)다. 상수는 세 모듈에 나뉜다:
기존 7개 지표의 임계값·대상·allowlist(`JSON_CALL_ALLOWLIST`)는
`scripts/_design_ratchet_scan.py` 상단에, 중복 헬퍼 지표의 임계값(`CLONE_MIN_AST_NODES`)과
`CLONE_ALLOWLIST` 는 `scripts/_design_ratchet_clones.py` 상단에, 지표 모델·`file_loc`
임계값·밴드 환산은 `scripts/_design_ratchet_contracts.py` 에 선언되어 있다.

## 사용

```bash
python scripts/design_ratchet.py                    # 위반 리포트 (위반 있으면 exit 1)
python scripts/design_ratchet.py --update-baseline  # delta 출력 후 baseline 재생성
pytest -q tests/test_design_ratchet.py              # CI 게이트와 동일한 검사
```

`--update-baseline` 은 덮어쓰기 **전에** delta 를 출력한다: 증가(위반) 목록, 신규 등장 /
사라진 파일, 지표별 감소 총량. 리뷰어가 "무엇이 왜 올라갔는지"를 PR 본문과 대조할 수 있게
이 출력을 붙인다. 구 baseline 이 현재 지표 스키마로 해석되지 않으면(지표 추가·개명) delta
를 생략하고 안내만 출력한 뒤 저장한다.

### baseline 표류 안내 (stale · slack)

검사 경로는 **실패시키지 않는 안내 두 가지**를 출력한다. 둘 다 래칫 계약상 통과지만
(삭제·감소는 항상 허용) 조용히 두면 래칫이 샌다. CLI 는 stdout 으로, pytest 게이트
(`test_current_repository_does_not_exceed_baseline`)는 `warnings.warn` 으로 같은 문구를
낸다 — 통과해도 `pytest -q` 의 warnings summary 에 남아 CI 로그에서 보인다. exit code 와
게이트 판정은 어느 쪽도 바뀌지 않는다.

1. **stale**: baseline 에만 있고 디스크에 없는 경로.
2. **slack**: 현존 파일에서 baseline 이 현재 스캔보다 느슨한 양(= 회수했지만 잠그지 않은
   감소분). 사라진 경로의 감소분은 stale 이 이미 지목하므로 slack 집계에서 제외한다.

```
경고: baseline 에만 있고 디스크에 없는 파일이 있습니다(위반 아님).
  남겨 두면 같은 경로가 다시 생길 때 예전 allowance 를 상속합니다 — `python scripts/design_ratchet.py --update-baseline` 으로 정리하세요.
  정리 대상 1개: app/services/gone.py
안내: baseline 이 현재 스캔보다 느슨합니다(위반 아님).
  회수한 감소분을 잠그려면 `python scripts/design_ratchet.py --update-baseline` 을 실행하세요.
  미회수 감소: json_direct_calls -4, dict_boundary_functions -2
```

**리스크: 삭제 → 동명 재생성 allowance 상속.** 파일을 지워도 그 경로의 카운트는 baseline
에 남는다. 나중에 **같은 경로**로 파일이 다시 생기면 예전 allowance 를 그대로 물려받아
그만큼의 위반이 무료로 통과한다. `--update-baseline` 은 이 잔여 항목을 정리하므로, 파일을
삭제·이동한 PR 은 경고가 뜨면 재생성을 함께 커밋한다. slack 도 같은 성격이다 — 부채를
줄여 놓고 잠그지 않으면 그만큼의 재악화가 무료로 통과한다.

CI(`.github/workflows/ci.yml`)는 `pytest -q tests/` 로 이 게이트를 돌고, `ruff check` 대상에
래칫 세 모듈(contracts · scan · CLI)이 포함된다.

## 실패했을 때

```
설계 래칫 위반 2건 (baseline -> current):
  json_direct_calls: app/services/foo.py 0 -> 2
  dict_boundary_functions: app/services/foo.py 3 -> 4
```

우선순위대로:

1. **위반을 없앤다(기본).** 함수 분해, `app/schemas/_base.py` 의 `StrictModel` 로 DTO
   경계 승격, `model_validate_json`/`model_dump_json` 으로 `json` 직접 호출 대체.
2. 정당한 경우에만 `python scripts/design_ratchet.py --update-baseline` 으로 baseline 을
   올리고 **PR 본문에 사유 + delta 출력을 남긴다**. 사유 없는 baseline 상승은 리뷰에서
   되돌린다.

내가 만지지 않은 파일이 실패에 나오는 등 **무관한 실패로 보이면**, `app/`·`scripts/` 에
파싱 불가한 작업 중(WIP) 파일이 남아 있는지 확인한다(`RatchetScanError` 는 파일명을 함께
출력한다). 스캔은 파싱 실패를 침묵시키지 않으므로 게이트 전체가 그 파일에서 멈춘다.
`file_loc_band` 위반의 숫자는 LOC 가 아니라 25줄 밴드다(위반 메시지에 환산이 붙는다).

### 정당한 baseline 상승 사유

- **파일 분해·이동(rename)**: 큰 파일을 쪼개면 기존 위반이 새 경로로 "신규 등장"해 파일별
  래칫에는 증가로 보인다. 이 경우 `--update-baseline` 이 정당하다. 단 PR 본문에 **지표
  총계가 늘지 않았음**을 delta 출력(사라진 파일의 감소 총량 vs 신규 파일의 카운트)으로
  첨부한다. 총계가 늘었다면 그것은 분해가 아니라 추가다.
- **지표 추가·개명**: 구 baseline 에 없는 지표는 기본값 0 이라 전 파일이 즉시 위반이 된다.
  지표를 손대는 PR 은 `--update-baseline` 을 함께 커밋한다.
- 그 밖의 상승은 원칙적으로 부채 증가다. 근거와 후속 정리 계획을 함께 적는다.

## allowlist 추가 기준

`JSON_CALL_ALLOWLIST` 는 "직렬화 자체가 그 모듈의 책임"인 경우에만 넣는다. 현재 항목은
`app/services/ml_release/signing.py` 하나이고, 서명 대상 바이트 정합성을 직접 통제해야
하기 때문이다. "여기는 고치기 귀찮다"는 allowlist 사유가 아니다 — 그 경우는 baseline
카운트로 남겨 두고 나중에 줄인다(baseline 은 줄어드는 것을 항상 허용한다).

### `CLONE_ALLOWLIST`

통합하면 안 되는 그룹의 면제 목록이다. 키는 **멤버 신원**(`"파일:함수"` 정렬 후 `|`
결합)이지 콘텐츠 해시가 아니다. 해시로 잡으면 면제된 함수를 사소하게 고칠 때마다 키가
바뀌어 빌드가 깨진다. 멤버 신원이면 본문 수정은 통과하고, 복사본이 하나 더 생기면 멤버
집합이 달라져 면제가 풀린다.

등재됐지만 현재 스캔에 더 이상 없는 항목은 `python scripts/design_ratchet.py` 가
실패시킨다. 죽은 항목을 방치하면 면제 범위가 조용히 넓어지기 때문이다.

## 탐지기 변경 이력

- **웨이브 2 (탐지기 강화)**: `unvalidated_dict_tasks` 승격 면제를 "weak 파라미터를 **위치
  인자/splat 대상**으로 받는 승격 + 중첩 정의 제외"로 좁혔다(미탐 잔여: `Thread(**payload)`;
  오탐 잔여: comprehension 변수·지역 재바인딩·keyword 형태 — 위 잔여 오차 참조).
  `env_test_sniff` 에 리터럴 컨테이너 멤버십(`in ("test", "ci")`)과 `os.environ`/`os.getenv`
  읽기(keyword 키 포함)를 추가했다(미탐 잔여: 변수·헬퍼로 한 번 감싼 판정, Compare 가 아닌
  집합 연산, 지역 상수 집합 우회). 선언 데이터(`NON_DELIVERING_ENVIRONMENTS`) 멤버십은
  의도적으로 계속 미탐이다. 검사 경로에 stale/slack 안내를 추가하고(게이트는
  `warnings.warn`) 데이터 계약을 `_design_ratchet_contracts.py` 로 분리했다. 저장소 실측
  기준 탐지 변경으로 인한 신규 위반은 0 이었고, 같은 커밋의 baseline 재생성은 레인 A~D 의
  감소분만 잠갔다.
