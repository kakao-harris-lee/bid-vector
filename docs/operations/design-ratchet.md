# 설계 래칫 (design ratchet)

- 측정 코어: `scripts/_design_ratchet_scan.py` (AST 스캔 · 데이터 계약 · 순수 비교)
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
| `env_test_sniff` | `…ENVIRONMENT` 와 `"test"` 를 비교하는 Compare 노드 수(`app/` 만) | 프로덕션 코드가 테스트를 스니핑하는 지점 |
| `unvalidated_dict_tasks` | celery task 중 검증되지 않는 입력을 받으면서 본문에 `model_validate` 도 `Model(**payload)` 승격도 없는 함수 수 | 재배달 payload 가 검증 없이 흐르는 지점 |

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

### `env_test_sniff` 의 탐지 범위(한정)

`X.ENVIRONMENT` **속성 접근**과 문자열 리터럴 `"test"` 가 같은 `Compare` 노드에 있는 경우만
센다(`settings.ENVIRONMENT == "test"`, `config.ENVIRONMENT != "test"`). 따라서 다음은
**미탐**이다: `settings.ENVIRONMENT in {"test", "ci"}` 같은 집합 멤버십,
`env = settings.ENVIRONMENT` 후 변수 비교, `os.getenv("ENVIRONMENT") == "test"`, 헬퍼 함수로
감싼 판정. 이 지표는 "테스트 스니핑 총량"이 아니라 **가장 흔한 형태의 증가를 막는 래칫**이다.

스캔 대상은 `app/`·`scripts/` 의 `*.py`(`__pycache__` 제외)다. 임계값·대상·allowlist 는
모두 `scripts/_design_ratchet_scan.py` 상단 상수로 선언되어 있다.

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

CI(`.github/workflows/ci.yml`)는 `pytest -q tests/` 로 이 게이트를 돌고, `ruff check` 대상에
스캐너 두 모듈이 포함된다.

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
