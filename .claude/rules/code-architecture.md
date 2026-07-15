---
globs:
  - "app/**/*.py"
  - "scripts/**/*.py"
  - "frontend/src/**/*.{ts,tsx}"
---

# Code Architecture & Pattern Enforcement

애플리케이션 로직 파일(백엔드 Python, 프론트 TS/React)을 수정할 때 지키는 설계 표준입니다.
회귀와 스파게티화를 막기 위해 **값·규칙·특수케이스를 선언적 데이터로 모으고, 코드는 그것을 해석만** 합니다.
상위 요약은 `CLAUDE.md` §4.5에 있고, 이 파일은 파일 유형별 구체 예시를 담습니다.

## 1. 선언적 구성 (Declarative Configuration)

동작·타이밍·리트라이·라우팅을 좌우하는 값은 함수 안 리터럴로 두지 않고 중앙 설정에 선언해 주입합니다.

- 백엔드 런타임·환경 값(타임아웃, 큐, 청크 크기, 토글) → `app/core/config.py`의 pydantic `Settings`.
- 교차 모듈 도메인 상수·값 집합 → `app/core/constants.py`.
- 프론트 상수·설정 → `frontend/src/shared/`(또는 feature 로컬 config).

```python
# Bad — 매직값이 함수 안에 하드코딩
def enqueue(task):
    task.apply_async(expires=86400, time_limit=1800)

# Good — Settings에서 주입
def enqueue(task, settings: Settings):
    task.apply_async(
        expires=settings.CELERY_RESULT_EXPIRES_SECONDS,
        time_limit=settings.CELERY_TASK_TIME_LIMIT_SECONDS,
    )
```

```typescript
// Bad
setTimeout(refetch, 5000);

// Good — shared config로 주입
import { queryConfig } from "@/shared/config";
setTimeout(refetch, queryConfig.refetchIntervalMs);
```

## 2. 상태·흐름 관리 (Zero If-Else Policy)

복잡한 조건 분기 트리를 만들지 않습니다. 값 기반 라우팅은 **키 → 함수/값 룩업**으로,
프로세스 흐름은 **상태 전이표/FSM**으로 표현합니다. (단일 가드 `if`는 허용 — 트리로 자라는 분기만 금지)

```python
# Bad — 값 기반 분기가 트리로 자람
if action == "create":
    do_create()
elif action == "update":
    do_update()
elif action == "cancel":
    do_cancel()

# Good — 룩업 디스패치
ACTION_HANDLERS = {
    "create": do_create,
    "update": do_update,
    "cancel": do_cancel,
}
handler = ACTION_HANDLERS.get(action)
if handler is None:
    raise ValueError(f"unsupported action: {action}")
handler()
```

```typescript
// Good — 룩업 테이블
const ACTION_MAP = {
  create: doCreate,
  update: doUpdate,
} as const;
ACTION_MAP[action]?.();
```

## 3. 예외·특수케이스는 데이터로 (config/YAML/DSL)

자주 바뀌거나 운영자가 튜닝하는 특수 규칙(게이트 키워드, 발주처 밴드, 카테고리 라우팅, 면허 별칭 등)은
코드 분기로 흩뿌리지 않고 **선언적 데이터**로 모으고, 코드는 로더/해석기만 유지합니다.
규칙이 커지면 상수 테이블·모델 필드에서 **YAML/DSL descriptor**로 승격합니다.

- 실제 예: 해양 세그먼트 게이트의 `required_keywords`(OR)·`focus_categories`는 코드 `if`가 아니라
  전략 **데이터**로 선언되고(`scripts/seed_marine_gate.py`) 매처가 해석합니다.
  새 세그먼트는 코드가 아니라 데이터를 추가해 확장합니다.

```yaml
# 규칙이 커지면 이런 선언으로 승격 — 얇은 로더가 읽어 매칭만 수행
segments:
  marine_engineering:
    match: any            # any = OR, all = AND
    required_keywords: ["항만", "어항", "방파제", "해양"]
    focus_categories: ["construction", "engineering_service"]
    exclude_keywords: ["하수관로"]
```

세그먼트 추가·수정은 위 데이터 편집으로 끝내고, 코드 분기는 늘리지 않습니다.
