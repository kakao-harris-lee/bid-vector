# Python ML · Kotlin Service · React Web 모듈 분리 계획

- 기준일: 2026-08-11
- 기준 코드: `ddee938` (`main`)
- 상태: 목표 방향 확정 — 세부 경계와 전환 게이트 승인 전 구현 착수 금지
- 성격: 아키텍처 결정 및 단계적 마이그레이션 계획
- 최상위 목표: 언어 교체가 아니라 회귀 방지와 금전 도메인 안전성 확보
- 로드맵 관계: 제품 우선순위는 `docs/roadmap.md`가 기준이며, 이 문서는 기술 전환 방법만 정의한다.

## 1. 결론

제안한 방향은 **목표 구조로 적합**하다. 이 결정의 이유는 Python의 성능 문제가 아니라, 비대해진 서비스에서 금액·비율·자격·정산 규칙의 변경 영향 범위를 제한하고 회귀를 구조적으로 차단하기 위해서다. 다음처럼 해석한다.

1. `web`은 React를 유지하고 독립 빌드·배포한다.
2. `service`는 Kotlin 기반의 **하나의 모듈형 모놀리스**로 시작한다. 처음부터 업무 영역별 마이크로서비스로 쪼개지 않는다.
3. `ml`은 Python이 학습뿐 아니라 모델 전처리, 추론, 임베딩, 평가, 릴리스까지 소유한다.
4. Kotlin과 Python은 코드 import나 공유 ORM 모델이 아니라 버전이 있는 HTTP/event 계약으로 연결한다.
5. 현재 FastAPI를 한 번에 Kotlin으로 다시 쓰지 않고, 같은 `/api/v1` 경로 뒤에서 기능 단위로 소유권을 옮기는 strangler 방식으로 전환한다.

즉, **언어별 폴더 재배치나 직역에는 반대하고, 도메인 불변식·계약·데이터 소유권을 먼저 세운 뒤 배포 단위를 분리하는 계획에는 찬성**한다.

Kotlin은 회귀를 자동으로 막아 주지 않는다. `Double`, 공용 `Map<String, Any>`, 거대한 `common`, 모듈 간 내부 참조를 허용하면 현재 문제가 다른 언어로 복제된다. 따라서 다음 항목이 모두 충족돼야 전환이 성공한 것이다.

- 금액, 비율, basis, VAT 의미가 값 객체와 타입으로 강제된다.
- 법정 하한, 자격, 투찰, 정산 상태 전이가 Kotlin 도메인의 단일 구현만 가진다.
- Python ML은 예측과 근거를 제공하지만 최종 금전 결정을 쓰지 못한다.
- 모듈 의존 방향과 크기 예산을 CI가 검사한다.
- 기존 회귀를 모은 golden/differential test가 전환 전후에 동일하게 통과한다.
- 모든 최종 결정이 입력 snapshot, 정책 버전, 모델 릴리스, 반올림 결과까지 재현 가능하다.

현재 제품 검증을 멈추는 전면 재작성은 하지 않는다. 계약과 characterization test를 먼저 현재 Python에 적용하고, 검증된 vertical slice만 Kotlin으로 옮긴다. 이 방식이면 Kotlin 구현이 지연돼도 회귀 방지 장치는 현재 시스템에 남는다.

## 2. 현재 구조에서 확인한 사실

### 2.1 규모와 결합도

| 영역 | 현재 상태 | 분리 시 의미 |
| --- | --- | --- |
| HTTP API | `app/api/` 29개 Python 파일, 약 5,359줄, route decorator 130개 | endpoint별 소유권 목록과 호환성 테스트가 먼저 필요 |
| 업무 서비스 | `app/services/` 231개 Python 파일, 약 50,652줄 | 폴더 전체를 Kotlin으로 번역하는 방식은 범위가 너무 크고 경계도 부정확 |
| ML/AI | `app/ai/` 43개 Python 파일, 약 8,721줄 | Python에 남길 코어가 이미 있으나 DB·업무 orchestration과 접점 정리가 필요 |
| 비동기 작업 | `app/tasks/` 14개 Python 파일, 약 3,137줄, Celery task 24개 | ML 작업과 ops 작업의 실행기/프로토콜 분리가 필요 |
| 데이터 | SQLAlchemy 모델 34개, Alembic revision 27개 | Alembic과 Flyway가 같은 테이블을 동시에 소유하면 안 됨 |
| 웹 | React/TypeScript 소스 289개, API 모듈 22개, 테스트 48개 | UI 코드는 이미 분리도가 높으며 배포 경계만 FastAPI에서 떼면 됨 |

이 수치는 파일을 단순 이동하는 작업이 아니라 **업무 계약과 데이터 소유권을 재설계하는 마이그레이션**임을 보여준다. 단계마다 다시 측정하며 기준 SHA와 함께 갱신한다.

### 2.2 이미 준비된 경계

- React는 `BUILD_TARGET=user|admin`으로 `/dashboard`와 `/admin`의 별도 번들을 만든다.
- `frontend/src/shared/types/openapi.d.ts`와 `check:sync-types`가 API 타입 드리프트를 검사한다.
- ML 작업은 inference, backfill, training, reevaluation 큐로 분리되어 있다.
- `semantic_input.changed` outbox, `embedding.ready`, similarity read model이 수집 트랜잭션과 추론 실행을 분리한다.
- `app/ai/service_interfaces.py`의 `PricePredictionPort`, `BidRecommendationPort`, `DocumentAnalysisPort`가 원격 ML adapter로 바꿀 수 있는 초기 seam을 제공한다.
- 사용자 GET 경로는 저장된 snapshot/read model을 우선 소비하고, 무거운 ML은 비동기 task로 넘기는 방향이 이미 적용되어 있다.

이 경계들은 Kotlin 전환의 선행 기반으로 재사용한다.

### 2.3 아직 분리되지 않은 경계

- `PredictionWorkflowService`는 FastAPI 예외, SQLAlchemy 조회/commit, 운영자 컨텍스트, ML port 호출, 결과 영속화를 한 흐름에서 수행한다.
- `opportunity_analysis`, `paper_bidding_backtest`, `synthetic_experiment`에는 모델 계산과 업무 정책·DB 접근이 함께 있다.
- Python 서비스가 프로젝트, 예측, 결정, 알림, 분석, ML 상태 테이블을 같은 DB session으로 읽고 쓴다.
- RabbitMQ를 사용하지만 외부 계약은 Celery task wire format과 Python task 이름에 가깝다. Kotlin이 Celery 메시지를 직접 만들도록 해서는 안 된다.
- React 산출물은 별도 번들이지만 FastAPI 컨테이너가 정적 파일까지 서빙한다.
- 현재 OpenAPI 타입 생성의 원천은 실행 중인 FastAPI app이다. 두 백엔드가 공존하면 하나의 명시적 계약 파일이 필요하다.

### 2.4 금전 도메인에서 확인한 회귀 원인

현재 코드는 이미 회귀 원인을 알고 방어를 시작했다.

- 기준 SHA 직전 #350~#359 변경의 대부분이 투찰금액 basis, 법정 하한, 비율 분모, 추정가격 출처 오염을 바로잡는 수정이다. 이는 같은 숫자를 다른 의미로 대입하는 문제가 일회성이 아님을 보여준다.
- `app/domain/money.py`의 `BaseAmount`와 `YegaAmount`는 mypy에서 교차 대입을 잡지만 `NewType(float)`라 런타임에는 같은 `float`다.
- `app/domain/rate_normalization.py`에는 저장 경로에 따라 `0.875`와 `87.5`가 혼재했고, 스케일 판별 임계치도 여러 곳에서 달랐다는 회귀 기록이 있다.
- `BudgetEstimate`의 VAT 의미도 코드 안에서 완전히 고정되지 않았다. `app/domain/money.py`는 부가세 포함으로 설명하지만 여러 public schema와 `bid_base`는 부가세 별도로 설명하고, schema 자체도 과거 저장값이 일관되지 않다고 명시한다.
- `Project.budget_estimate`, `HistoricalData.base_amount`, `PricePrediction.predicted_price`, `BidDecisionRecord.submitted_bid_amount`, `TenderResult.winning_amount` 등 핵심 금액이 SQLAlchemy `Float`로 저장된다.
- `pyproject.toml`은 순수 domain과 일부 커널만 strict island로 검사하고 `app.*` 전체는 아직 `ignore_errors = true`다. 좋은 래칫이지만 전역 강제력은 아니다.
- basis/floor/settlement characterization와 golden test가 이미 있으므로, 이를 폐기하지 않고 언어 간 differential corpus의 시작점으로 삼을 수 있다.

따라서 비대화의 해결책은 파일 수를 줄이는 것만이 아니다. **숫자의 의미와 상태 전이를 타입·모듈·저장소·테스트 네 층에서 동시에 고정**해야 한다.

## 3. 목표 구조

```text
Browser
   │
   ├── /dashboard, /admin ──► React static hosting
   │
   └── /api/v1 ─────────────► Gateway / reverse proxy
                                   │
                         ┌─────────┴─────────┐
                         │                   │
                  Kotlin service-api    Python ml-platform
                  - auth/RBAC           - feature transform
                  - operator/profile     - training/evaluation
                  - project/bid          - online inference
                  - strategy/decision    - embedding/similarity
                  - KONEPS integration   - model release/artifact
                  - notifications        - ML workers
                  - public API/BFF
                         │                   │
                         ├──── HTTP ─────────┤  짧은 동기 추론
                         └──── RabbitMQ ─────┘  장시간 command/event
                                   │
                         versioned event contracts

PostgreSQL
   ├── service schema/role: Kotlin만 write
   └── ml schema/role: Python만 write

Object storage
   └── signed model manifest + immutable artifact
```

초기에는 한 저장소와 한 PostgreSQL instance를 유지해도 된다. 중요한 것은 저장소나 서버 수가 아니라 **writer와 계약이 하나인지**다.

## 4. 모듈별 소유권

### 4.1 React `web`

소유한다.

- 사용자/관리자 화면, 라우팅, client-side 상태
- API 호출과 오류 표현
- OpenAPI에서 생성한 TypeScript 타입
- `/dashboard`, `/admin` 독립 번들

소유하지 않는다.

- 자격·가격·법정 하한·투찰 판단 규칙
- 보안 인가 판단
- ML feature 계산 또는 모델 선택
- 서버 작업 상태의 임의 추정

현재 `frontend/`는 즉시 이름을 바꾸지 않는다. 정적 호스팅 분리가 완료되고 CI 경로가 안정된 뒤 `web/`으로 이동한다.

### 4.2 Kotlin `service-api`

하나의 배포 가능한 **Gradle 멀티모듈 모놀리스**로 시작한다. 아래 모듈은 폴더 분류가 아니라 컴파일 의존성과 데이터 writer를 제한하는 경계다.

| 모듈 | 소유하는 도메인 | 소유하지 않는 것 |
| --- | --- | --- |
| `identity` | 로그인, token, 역할, operator scope | 회사 전략, 입찰 판단 |
| `operator` | 회사 프로필, 전략, 온보딩, 알림 채널 메타데이터 | 인증 구현, ML feature |
| `procurement` | KONEPS 공고·개찰 canonical facts, 수집 상태, 외부 adapter | 예측, 최종 투찰 판단 |
| `qualification` | 지역·면허·실적·PQ 자격 판정 | 모델 confidence |
| `bidding` | 투찰 계획, 제출 기록, allocation, 상태 전이 | 모델 학습, 알림 전송 |
| `decision` | 법정 하한, capacity/workload, `bid_now/review/skip`, 근거 | ML 추론 구현 |
| `settlement` | 개찰 결과 대사, paper/actual bid outcome, 정정 이력 | 결제 원장 |
| `notification` | 알림 정책, delivery outbox, 전송 결과 | 결정 규칙 재계산 |
| `operations` | 작업 상태, 운영 read model, 감사/evidence | 업무 aggregate 변경 |
| `ml-gateway` | Python ML HTTP/event adapter, timeout, circuit breaker | 최종 업무 결정 |
| `application` | transaction, use-case 조합, public API wiring | 새로운 업무 규칙 |

실제 결제·수수료·예치금이 생기면 `billing-ledger`를 별도 모듈로 추가한다. 입찰 결과 테이블을 잔액 원장처럼 재사용하지 않는다. 돈의 이동을 기록하는 시점부터는 append-only 분개와 역분개, 잔액 불변식을 별도 ADR로 정의한다.

각 업무 모듈 내부는 `domain → application → adapter` 방향을 따른다.

- `domain`은 Kotlin 표준 라이브러리와 승인된 작은 `shared-kernel`만 사용한다. Spring, JPA/jOOQ, JSON, HTTP, RabbitMQ를 import하지 않는다.
- 다른 모듈은 공개 command/query/event 계약만 호출한다. 상대 모듈의 repository, table record, `internal` 구현을 참조하지 않는다.
- aggregate 변경은 application command와 하나의 transaction을 거친다. controller, scheduler, consumer가 repository를 직접 호출하지 않는다.
- `shared-kernel`에는 `Money`, `Rate`, `Basis`, 식별자, 시간 타입만 둔다. 범용 `utils`, 공용 entity, 공용 repository를 넣지 않는다.
- 모듈별 table writer는 하나다. 읽기 편의를 이유로 다른 모듈 테이블을 update하지 않는다.
- ArchUnit 계열 architecture test와 Gradle dependency rule로 이 규칙을 CI에서 강제한다.

금전 도메인은 다음 값 객체를 기본으로 한다.

- 최종 원화 금액은 `Long` 원 단위로 저장한다. 계산 중 소수와 세율은 `BigDecimal`을 사용하며 `Double`로 변환하지 않는다.
- `Money(amount, currency, basis, vatTreatment)`는 basis 없는 원시 숫자를 받지 않는다.
- `BidRate`는 내부 표현을 fraction 하나로 고정한다. percent 입력은 API adapter에서 명시적으로 변환하고 원문 단위도 증적에 남긴다.
- 반올림은 `RoundingPolicy(scale, mode, stage, version)`로 명명한다. 각 호출부의 `round()`를 허용하지 않는다.
- `LegalFloor`, `EligibilityPolicy`, `DecisionPolicy`는 효력 시작일과 버전을 가진다. 과거 결과 재현 시 당시 버전을 사용한다.
- 최종 투찰/정산 기록에는 입력 snapshot hash, 정책 버전, 모델 release id, 계산 전후 금액, actor, correlation id를 보존한다.

Kotlin 쪽 기본 기술 선택은 별도 ADR로 확정하되, 첫 후보는 Spring Boot, Spring Security, jOOQ, Flyway, RabbitMQ, Micrometer/OpenTelemetry, Testcontainers 조합이다. 비동기·reactive stack은 측정된 필요 없이 먼저 도입하지 않는다.

KONEPS browser fallback은 ML이 아니라 procurement adapter다. 이 경로를 Kotlin으로 즉시 포팅하는 비용이 크면 일시적으로 Python collector adapter를 유지할 수 있다. 언어 순도보다 API quota, 파싱 회귀, 운영 복구 가능성이 우선이다.

### 4.3 Python `ml-platform`

Python은 **학습만** 담당하지 않는다. 학습과 serving의 전처리/모델 차이를 막기 위해 다음을 함께 소유한다.

- 학습 데이터 검증과 feature schema
- 모델 학습, calibration, backtest, holdout 평가
- 가격 예측과 문서 분석의 모델 추론
- embedding 생성과 모델 기반 similarity
- model registry/release manifest, checksum, signature, rollback
- 장시간 backfill/training/reevaluation worker
- 모델 release id와 입력/출력 provenance

Python 내부도 다음 역할 모듈로 나누고 import 방향을 고정한다.

```text
ml-platform/
  contracts/       # Pydantic 요청·응답·event 모델, ORM 없음
  datasets/        # versioned snapshot/label 검증
  features/        # 학습·추론 공용 순수 feature transform
  training/        # 학습 orchestration
  evaluation/      # holdout/backtest/calibration
  inference/       # online/batch 추론 use case
  embeddings/      # embedding과 모델 기반 similarity
  registry/        # artifact manifest, promote, rollback
  workers/         # Celery/RabbitMQ adapter
  adapters/        # DB snapshot, object storage, HTTP
```

- `training`과 `inference`는 같은 `features`와 `contracts`를 사용한다.
- 순수 코어는 FastAPI, Celery, SQLAlchemy를 import하지 않는다.
- Python은 Kotlin service ORM/table을 import하거나 직접 commit하지 않는다.
- import-linter/mypy/ruff와 파일·함수 예산 래칫으로 새 순환 의존과 비대화를 차단한다.
- 모델 출력에 법정 하한이나 자격을 최종 적용하지 않는다. 필요한 입력 feature와 예측·불확실성·근거만 반환한다.

다음은 Kotlin으로 귀속한다.

- 사용자 예산, 자격, 법정 하한, 전략, workload에 따른 최종 업무 결정
- operator scope와 권한
- bid/decision/notification의 canonical persistence
- 사용자에게 노출할 최종 응답 조립

현재 Python의 deterministic guardrail과 selector는 바로 옮기지 않는다. 먼저 golden test로 의미를 고정한 뒤, 모델 종속 계산은 Python에 남기고 업무 정책만 Kotlin으로 한 규칙씩 이전한다. 같은 규칙을 두 언어에서 장기간 동시에 실행하지 않는다.

추론을 Kotlin에서 직접 실행하려면 ONNX 같은 이식 가능한 artifact, 동일 전처리, golden corpus 동등성, release checksum 검증이 모두 확보된 경우에만 별도 결정한다. 기본안은 Python serving이다.

## 5. 계약 원칙

### 5.1 Public HTTP 계약

- 외부 경로는 전환 중에도 `/api/v1`을 유지한다.
- `contracts/public/openapi.yaml`을 명시적 단일 원천으로 만든다.
- FastAPI와 Kotlin controller가 모두 이 계약에 대한 provider test를 통과해야 한다.
- React 타입은 실행 중인 특정 backend가 아니라 이 계약 파일에서 생성한다.
- endpoint를 Kotlin으로 옮겨도 path, method, status, nullability, pagination, 오류 body를 임의로 바꾸지 않는다.
- 제거는 deprecation 기간과 실제 React/운영 호출 확인 후 별도 변경으로 수행한다.

### 5.2 Kotlin ↔ Python ML 동기 계약

짧고 사용자 응답에 필요한 추론만 HTTP로 호출한다. 요청에는 최소한 다음을 포함한다.

- `request_id`, `correlation_id`
- `feature_schema_version`
- canonical input facts와 amount basis/provenance
- 요구 model/release selector
- timeout/deadline

응답에는 최소한 다음을 포함한다.

- `model_release_id`, artifact checksum
- prediction/candidates와 단위·basis
- confidence/quality flags
- model reason codes
- `feature_schema_version`
- 재시도 가능 여부를 구분하는 오류 코드

ML 응답은 최종 `bid_now/review/skip` 권한을 갖지 않는다.

### 5.3 비동기 계약

- broker는 RabbitMQ를 유지할 수 있다.
- Kotlin이 Celery task wire format을 직접 생산하지 않는다.
- 서비스 간에는 JSON Schema 또는 Protobuf로 정의한 일반 AMQP envelope을 사용한다.
- Celery는 Python ML 내부 실행기로만 남길 수 있다.
- 모든 command/event는 `event_id`, `event_type`, `event_version`, `aggregate_id`, `aggregate_version`, `idempotency_key`, `correlation_id`, `causation_id`, `occurred_at`을 가진다.
- producer outbox와 consumer inbox/dedup을 사용해 at-least-once 전달에 안전하게 한다.
- 이벤트는 추가 필드에 관대하고, 기존 필드 삭제·의미 변경은 새 major version으로만 한다.

권장 이벤트 예시는 다음과 같다.

- `project.semantic-input.changed.v1`
- `ml.embedding.ready.v1`
- `ml.prediction.completed.v1`
- `ml.training.requested.v1`
- `ml.release.promoted.v1`
- `decision.recorded.v1`
- `notification.delivery.requested.v1`

### 5.4 금액·비율 계약

언어 경계에서 숫자 의미를 추론하지 않는다.

- 최종 원화는 `amount_krw` 정수로 전송한다. 소수가 필요한 계산 중간값은 JSON number가 아니라 scale이 명시된 decimal string과 단위를 사용한다.
- 모든 금액은 `basis`, `currency`, `vat_treatment`, `provenance`를 함께 가진다. 필드명이나 크기로 basis를 추측하지 않는다.
- 모든 비율은 `value`, `unit=fraction`, `scale`, `source`를 가진다. `87.5`와 `0.875`를 자동 판별하는 규칙은 신규 계약에서 금지한다.
- 반올림 시점·방식·정책 버전이 계약에 포함되지 않으면 최종 투찰 금액으로 승격할 수 없다.
- 알 수 없는 enum, 지원하지 않는 정책/feature schema, 누락된 provenance는 조용히 기본값으로 바꾸지 않고 명시적으로 거부한다.
- Python과 Kotlin은 같은 계약 파일에서 각자 DTO를 생성한다. 한 언어의 런타임 클래스를 다른 언어가 공유하지 않는다.
- monetary final field는 canonicalization 후 exact equality를 요구한다. tolerance는 모델 진단용 실수에만 허용한다.

## 6. 데이터 소유권과 migration

### 6.1 불변 규칙

1. 한 테이블에는 한 writer만 둔다.
2. Alembic과 Flyway가 같은 테이블 DDL을 동시에 관리하지 않는다.
3. 서비스 간 join을 위해 상대 schema에 write 권한을 주지 않는다.
4. dual write를 애플리케이션 코드에 넣지 않는다.
5. 상태 전달은 outbox/event 또는 검증된 CDC로 한다.
6. ML 학습은 운영 DB의 임의 live query가 아니라 versioned snapshot/export를 목표로 한다.
7. 금전 상태의 정정은 원본을 덮어쓰지 않고 정정 사유와 이전 값을 남긴다.
8. 최종 결정과 정산은 동일 입력·정책·모델 버전으로 재현 가능해야 한다.

### 6.2 금액 저장 규칙

현재 `Float` 컬럼을 이름만 바꾸거나 한 번에 type cast하지 않는다. 각 aggregate별로 expand → backfill → compare → constraint → read cutover → writer cutover → contract 순서를 따른다.

- 확정 원화 금액은 PostgreSQL `BIGINT` 원 단위를 기본으로 한다.
- 세율, 비율, 계산 중간값처럼 소수가 필요한 값은 scale이 명시된 `NUMERIC`을 사용한다.
- 금액과 함께 basis/VAT/provenance/policy version 컬럼 또는 불변 snapshot을 저장한다.
- backfill은 원본 `Float`를 보존하고 변환 결과·오차·거부 사유를 별도 report로 만든다. 근거 없는 반올림으로 오염을 숨기지 않는다.
- DB `CHECK`, `NOT NULL`, unique/idempotency constraint로 양수 금액, fraction 범위, 상태 전이 전제조건을 가능한 범위에서 강제한다.
- write 이전에는 구 값과 신 값의 원 단위 exact comparison, 하한 불변식, aggregate checksum을 검증한다.
- 결제/수수료 원장이 도입되면 append-only entry와 역분개를 사용하고 잔액은 entry 합계로 검증한다.

### 6.3 단계적 소유권 이전

각 테이블 묶음은 다음 절차를 독립적으로 거친다.

1. 현재 owner와 reader/writer 목록을 만든다.
2. 기존 동작을 characterization test로 고정한다.
3. Kotlin 소유 schema에 새 테이블을 Flyway로 생성한다.
4. PK와 시간 의미를 보존해 backfill한다.
5. row count, PK 집합, FK, 핵심 집계, immutable payload checksum을 비교한다.
6. 변경분은 outbox/CDC로 따라잡는다.
7. read를 shadow 비교한 뒤 Kotlin으로 전환한다.
8. 단일 writer를 Kotlin으로 전환한다. dual write 구간은 두지 않는다.
9. 관찰 기간과 rollback rehearsal 후 기존 Python write 경로를 제거한다.
10. 더 이상 참조하지 않는 구 테이블은 별도 승인 후에만 정리한다.

초기 이전 순서는 read model과 독립 테이블부터 시작하고, `projects`, `historical_data`, `bid_decision_records`처럼 여러 계산이 의존하는 중심 테이블은 뒤로 둔다.

## 7. 단계별 실행 계획

### Phase 0 — 결정 게이트와 기준선

목표: 어떤 회귀를 막고 어떤 불변식을 보존할지 합의한다.

- 130개 endpoint를 domain, React consumer, 외부 consumer, read/write, auth 수준, DB table, task 호출 기준으로 inventory한다.
- 24개 task를 ML, procurement, notification, evidence, scheduler로 분류한다.
- 34개 모델의 owner/readers/writers와 transaction boundary를 기록한다.
- 현재 OpenAPI를 저장하고 대표 응답 golden fixture를 만든다.
- #350~#359를 포함한 금액 basis·비율 scale·법정 하한·출처 오염 회귀를 재현하는 regression ledger를 만든다.
- `Money`, `BidRate`, `LegalFloor`, 자격, 투찰, 정산 aggregate의 불변식과 정책 버전 규칙을 ADR로 승인한다.
- 현재 `Float` 금액 컬럼별 실제 단위, basis, VAT 의미, 결측/오염 비율과 소비자를 inventory한다.
- HTTP p95/p99, error rate, queue wait, RSS, 배포/복구 시간을 현재 기준선으로 측정한다.
- 회귀 방지를 Kotlin 전환의 최상위 성공 기준으로 ADR에 고정한다.

Exit gate:

- endpoint/task/table ownership 누락이 없다.
- 현재 기능 기준선, 금전 불변식, 목표 SLO가 승인되어 있다.
- 기존 주요 회귀가 자동 테스트에서 red → fix → green으로 재현된다.
- 전면 재작성 금지, endpoint 단위 routing, 단일 writer 원칙이 승인되어 있다.

### Phase 1 — 계약과 모노레포 골격

목표: 런타임 변경 없이 새 경계를 빌드·테스트할 수 있게 한다.

권장 최종 레이아웃은 다음과 같다.

```text
contracts/
  public/openapi.yaml
  ml/openapi.yaml
  events/
service-api/             # Kotlin modular monolith
ml-platform/             # Python training + serving + workers
web/                     # React; 초기에는 frontend/ 유지
infra/
docs/
```

- 먼저 `contracts/`와 Kotlin skeleton만 추가한다. 기존 Python 경로는 그대로 둔다.
- public OpenAPI와 ML internal OpenAPI의 lint/breaking-change gate를 CI에 추가한다.
- Kotlin, Python, React의 build/test를 독립 job으로 만든다.
- correlation id, structured log, trace propagation 규칙을 공통 계약으로 둔다.
- Kotlin 업무 모듈과 최소 `shared-kernel`을 Gradle subproject로 생성하고 architecture test를 추가한다.
- dependency rule을 자동 검사한다: React→public API, Kotlin domain→framework 금지, Kotlin→ML contract, Python ML→service ORM import 금지.
- 기존 `tests/test_large_function_budgets.py`와 같은 크기 래칫을 Kotlin/Python 양쪽에 두되, 초기 기준선보다 악화될 수 없고 단계별로 낮아지게 한다.

Exit gate:

- 세 영역이 서로 소스를 import하지 않고 독립 build된다.
- 계약 변경 없이도 기존 React 타입 생성이 재현된다.
- 빈 Kotlin service가 health/observability/Testcontainers 검증을 통과한다.
- 금지된 모듈 참조, domain framework import, 순환 의존을 일부러 넣은 fixture가 CI에서 실패한다.

### Phase 2 — React 배포 분리와 gateway

목표: 가장 낮은 위험으로 web 배포 생명주기를 분리한다.

- 기존 `/dashboard`, `/admin` base path와 full-page 경계를 유지한다.
- React artifact를 nginx/CDN/static container에서 서빙한다.
- `/api/v1`은 같은 origin의 reverse proxy를 사용해 CORS/cookie 변화를 최소화한다.
- FastAPI의 static serving은 fallback으로 유지한 뒤 제거한다.
- `tests/test_spa_serving.py`, Vitest, Playwright를 새 호스팅 경로에 맞게 이중 실행한다.

Exit gate:

- 두 번들의 asset과 route가 서로 섞이지 않는다.
- deep-link, refresh, auth expiry, WebSocket이 기존과 동일하게 동작한다.
- web만 독립 배포하고 즉시 이전 artifact로 rollback할 수 있다.

### Phase 3 — Python ML boundary 추출

목표: Kotlin을 쓰기 전에 ML을 원격 호출 가능한 독립 경계로 만든다.

- `PricePredictionPort`, `BidRecommendationPort`, `DocumentAnalysisPort`에 in-process adapter와 remote adapter를 둔다.
- prediction, embedding, training, reevaluation 계약을 DTO로 고정한다.
- `contracts/datasets/features/training/evaluation/inference/embeddings/registry/workers/adapters` 역할을 분리하고 import rule을 적용한다.
- FastAPI 업무 흐름이 ORM object가 아니라 canonical input DTO로 ML을 호출하게 한다.
- ML은 service table을 직접 commit하지 않고 결과를 반환하거나 event를 발행한다.
- training dataset은 versioned export/snapshot과 manifest로 전달한다.
- feature flag로 in-process와 remote Python ML을 전환한다.
- shadow mode에서는 remote 결과를 비교만 하고 DB write, notification, task fan-out을 하지 않는다.

Exit gate:

- deterministic field, basis/provenance, reason code가 golden corpus에서 일치한다.
- 금액은 승인된 domain rounding 후 일치하고, 진단용 실수는 사전 정의한 tolerance 안이다.
- release id/checksum이 모든 예측과 evidence에 남는다.
- ML 장애가 service thread/connection pool을 고갈시키지 않고 timeout/circuit breaker로 제한된다.

### Phase 4 — Kotlin read-only vertical slice

목표: 위험이 낮은 조회 경로로 gateway, auth 검증, DB mapping, 관측성을 검증한다.

권장 순서:

1. `/health`와 운영 metadata
2. dashboard/project read model
3. analytics/evidence 조회
4. 저장된 similarity/preview snapshot 조회

- gateway가 endpoint별로 FastAPI 또는 Kotlin에 routing한다.
- Kotlin은 기존 token을 동일한 claim 규칙으로 검증한다. 외부에서 들어온 임의 identity header를 신뢰하지 않는다.
- shadow read는 응답을 사용자에게 보내지 않고 status/schema/정렬/null 의미를 비교한다.
- public OpenAPI path는 바꾸지 않는다.

Exit gate:

- status, schema, pagination, ordering, 권한 결과가 호환된다.
- 관찰 기간 동안 error budget과 latency budget을 충족한다.
- route flag 하나로 FastAPI에 되돌리는 rollback을 실제 연습했다.

### Phase 5 — Kotlin write domain과 non-ML job 이전

목표: 업무 가치 단위로 writer를 한 번에 하나씩 옮긴다.

권장 순서:

1. operator profile/onboarding의 좁은 write slice
2. operations metadata/evidence와 notification outbox
3. KONEPS collection/persistence
4. qualification와 strategy/monitor orchestration
5. decision의 read/shadow 계산과 persistence
6. bid 제출 기록, allocation, settlement
7. token 발급과 credential ownership

금액을 확정하거나 실격 여부를 바꾸는 5~6번은 가장 마지막에 옮긴다. 먼저 Kotlin 결과를 저장·알림 없이 shadow 계산하고, 기존 Python 결과 및 승인된 golden corpus와 exact 비교한다.

각 slice는 API, domain rule, DB owner, event, scheduler, React consumer를 함께 옮긴다. controller 파일 단위나 공용 util 단위로 이전하지 않는다.

Celery task 중 ML 작업은 Python에 남기고, ops 작업은 Kotlin worker/scheduler로 옮긴다. Kotlin이 Python ML 장시간 작업을 요청할 때는 일반 AMQP command 또는 ML control API를 사용한다.

Exit gate:

- 해당 aggregate writer가 하나뿐이다.
- duplicate/out-of-order/redelivery 테스트를 통과한다.
- monetary final field, 법정 하한, 자격, 상태 전이가 exact differential test를 통과한다.
- 정책 버전과 입력 snapshot만으로 결정·정산 결과를 재현할 수 있다.
- dry-run 테스트에서 실제 Telegram/email 발송이 없다.
- 장애 주입 후 outbox/inbox 재처리로 상태가 수렴한다.

### Phase 6 — 데이터 소유권 분리와 cutover

목표: 언어 분리가 아니라 실제 runtime/data 독립성을 완성한다.

- `service`와 `ml` schema, DB role, migration history를 분리한다.
- Python ML의 service schema 직접 write를 차단한다.
- 필요한 학습 데이터는 versioned snapshot/export로 전환한다.
- similarity/prediction 결과는 ML-owned projection 또는 event를 통해 service read model로 전달한다.
- backup/restore, 재처리, release rollback을 서비스별로 검증한다.

Exit gate:

- 권한 검사로 교차 schema write가 실제 거부된다.
- clean database에서 Flyway와 Alembic이 각각 자기 schema를 재현한다.
- 데이터 검증 report가 100% key 보존과 승인된 집계 일치를 보인다.
- 구 Python service 없이 Kotlin service + Python ML + React E2E가 통과한다.

### Phase 7 — FastAPI service 퇴역

목표: Python에는 ML platform만 남긴다.

- access log와 코드 검색으로 구 endpoint consumer가 없음을 확인한다.
- deprecated alias를 공지된 순서로 제거한다.
- FastAPI의 auth, CRUD, static hosting, ops task를 제거한다.
- Python package dependency에서 서비스 전용 FastAPI/ORM import를 제거한다. ML control API에 FastAPI를 계속 쓰는 것은 허용한다.
- runbook, compose, README, roadmap의 current-state 설명을 갱신한다.

Exit gate:

- 공개 API는 Kotlin만 서빙한다.
- Python은 ML 계약 이외의 service DB/table을 알지 못한다.
- rollback 보존 기간 종료와 구 runtime 제거가 별도 승인되었다.

## 8. 검증 전략

### 8.1 계약 검증

- OpenAPI breaking-change 검사
- React generated type drift 검사
- Kotlin/Python provider contract test
- ML event schema compatibility test
- consumer-driven contract test

### 8.2 의미 동등성 검증

다음은 단순 JSON 모양이 아니라 golden corpus 결과가 같아야 한다.

- amount basis와 provenance
- 법정 하한 적용과 실격측 guardrail
- eligibility, 지역, 면허, budget rule
- price regime과 candidate selector
- `bid_now/review/skip` 및 reason code
- notification dry-run/owner isolation
- similarity top-K, snapshot 상태, stale 처리
- model release/checksum과 evidence 연결

금액·비율·최종 상태는 tolerance 비교를 사용하지 않는다. 입력을 canonical 단위로 변환하고 승인된 반올림 정책을 적용한 뒤 exact equality로 비교한다. 기존 Python이 오동작하는 fixture는 "현재 값"을 복제하지 않고, 회귀 이슈와 도메인 명세로 올바른 기대값을 먼저 승인한다.

### 8.3 데이터 검증

- PK/FK/unique/not-null 보존
- row count와 PK set 비교
- immutable JSON checksum 비교
- operator별 project/bid/decision/notification 집계 비교
- 시간대와 timestamp precision 비교
- 재실행 가능한 backfill 및 중단 후 resume 검증

### 8.4 운영 검증

- HTTP p50/p95/p99와 error rate
- RabbitMQ queue wait, redelivery, DLQ
- DB pool, lock, slow query
- Python model cold start와 RSS
- Kotlin heap/GC와 thread pool
- trace가 React request → Kotlin → Python ML → event까지 이어지는지 확인

개발 테스트 green은 운영 E2E 근거로 대체하지 않는다. 운영 서버에서는 같은 git SHA, image, model release, contract version을 증적에 남긴다.

### 8.5 회귀 방지 CI 게이트

| 게이트 | 매 PR | main/nightly | cutover 전 |
| --- | --- | --- | --- |
| compile/type/lint, 모듈 의존·순환·크기 래칫 | 필수 | 필수 | 필수 |
| domain unit + property test | 필수 | 필수 | 필수 |
| OpenAPI/event consumer-provider contract | 변경 영향 범위 | 전체 | 전체 |
| 금액·하한·자격·정산 golden/differential | 영향 범위 + 핵심 corpus | 전체 corpus | 전체 corpus |
| DB migration/backfill/replay/idempotency | 변경 영향 범위 | 실제 DB dialect | rehearsal |
| financial domain mutation test | 변경 모듈 | 전체 중요 규칙 | 승인되지 않은 생존 mutation 0 |
| shadow/E2E/장애 주입 | 해당 없음 | 제한 환경 | 필수 |

추가 원칙은 다음과 같다.

- property test는 금액이 법정 하한보다 작아지지 않음, percent/fraction 혼입 거부, 정산 terminal state의 불변성, 동일 idempotency key의 단일 효과를 검사한다.
- concurrency test는 중복 제출, out-of-order result, consumer redelivery, optimistic lock 충돌을 포함한다.
- 회귀가 발견되면 수정 전에 최소 재현 fixture를 regression ledger에 추가한다. 같은 유형의 실패가 다시 발생하면 모듈 경계 또는 타입 제약을 강화한다.
- 새 파일 분할만으로 예산을 통과하지 못하게 aggregate별 fan-in/fan-out, public API 수, 순환 의존, 함수 크기를 함께 측정한다.
- 정책 변경 PR은 정책 버전 증가, 영향받는 golden diff, migration/replay 결과, 승인자를 함께 남긴다.

### 8.6 Claude 구현 · Codex 리뷰 harness

- Claude Code의 `kotlin-builder`가 하나의 vertical slice를 구현하고 Gradle 검증까지 수행한다.
- Kotlin 파일에는 `.claude/rules/kotlin-service.md`의 모듈·금액 불변식이 자동 적용된다.
- 구현 diff는 `scripts/codex-review-kotlin.sh`가 Codex 전용 reviewer로 읽기 전용 리뷰한다.
- Codex 결과는 `reports/codex-review/`의 구조화된 JSON으로 남기며 Claude가 내용을 수정하지 않는다.
- `request_changes`면 같은 branch에서 수정·재검증·재리뷰한다. 자동 반복은 최대 2회이고 이후에는 사용자 판단으로 넘긴다.
- Gradle green과 Codex `approve`는 merge나 운영 cutover 승인이 아니다. 기존 single-writer 전환·사용자 승인 게이트를 그대로 따른다.

## 9. 주요 위험과 대응

| 위험 | 징후 | 대응 |
| --- | --- | --- |
| Kotlin 전환이 안전성으로 오인됨 | `Double`, raw string/Map, controller 업무 규칙이 재등장 | Money/Rate 값 객체, domain purity와 architecture test를 cutover 선행 조건화 |
| big-bang 재작성 | 장기간 사용자 가치 없이 두 구현이 벌어짐 | endpoint/domain slice별 route 전환 |
| 거대 공용 모듈 재발 | `common`, shared entity/repository가 모든 모듈에서 참조됨 | shared-kernel 허용 타입 제한, public API/fan-in 래칫 |
| 금액 단위·basis 혼입 | `87.5`/`0.875`, 예정가/기초금액을 값 크기로 추측 | 계약의 명시 단위·basis, 미상 값 fail-closed, exact golden |
| 반올림·정책 시간 회귀 | 같은 입력의 과거 결과가 현재 규칙으로 달라짐 | versioned rounding/policy와 effective date, replay test |
| training-serving skew | 학습은 맞지만 운영 예측이 달라짐 | Python이 전처리+추론까지 소유, release manifest 고정 |
| 업무 규칙 이중화 | Python/Kotlin 결정이 서로 다름 | golden test 후 single owner 전환, 장기 dual implementation 금지 |
| shared DB 결합 유지 | 서로 상대 테이블을 직접 update | schema/role 분리, single writer, event/read model |
| dual write 불일치 | 한쪽 write만 성공 | app dual write 금지, outbox/CDC 사용 |
| Celery 종속 | Kotlin producer가 Python task 내부 형식을 알아야 함 | 일반 AMQP/HTTP 계약, Celery는 Python 내부로 제한 |
| 인증 경계 오류 | route별 권한 결과 차이, identity header 위조 | 동일 token/claim contract, mTLS 또는 gateway 신뢰 경계 |
| React 배포 회귀 | base path, deep-link, WebSocket 실패 | URL 유지, same-origin proxy, SPA/E2E 이중 검증 |
| migration 도구 충돌 | Alembic/Flyway가 같은 DDL 변경 | schema/table owner 명시, ownership transfer gate |
| 운영 복잡도 증가 | 장애 위치와 책임이 불명확 | 공통 correlation/trace, SLO, runbook, 독립 rollback |
| 제품 검증 지연 | 전환이 Phase 3 실증을 막음 | roadmap 우선, 작은 architecture runway만 병행 |

## 10. 중단 및 rollback 기준

다음 중 하나가 발생하면 해당 phase의 route/write cutover를 중단한다.

- canonical 금액, basis, legal floor, selector 결과가 달라진다.
- monetary final field가 exact comparison에 실패하거나 `Double`/단위 추론 경로가 발견된다.
- 정책 버전, 반올림 규칙, 입력 snapshot 중 하나라도 증적에서 누락된다.
- operator scope나 권한 결과가 더 넓어진다.
- event 유실, 중복 notification, silent drop이 발생한다.
- model release/checksum을 추적할 수 없다.
- 승인된 latency/error budget을 초과한다.
- 데이터 count/checksum/FK 검증이 일치하지 않는다.
- 실제 사용자 알림이나 운영 schedule이 검증 중 의도치 않게 실행된다.

rollback은 코드 되돌리기만 뜻하지 않는다.

- read route: gateway를 이전 provider로 전환한다.
- write route: 단일 writer 전환 지점을 되돌리고 outbox offset을 보존한다.
- schema: expand 단계의 호환 컬럼/테이블은 관찰 기간 동안 제거하지 않는다.
- ML: 이전 signed manifest와 artifact checksum으로 되돌린다.
- React: 직전 immutable artifact를 재배포한다.

## 11. 권장 착수 순서

현재 바로 실행할 가치가 있는 범위는 다음 다섯 가지다.

1. #350~#359 및 금액/하한/정산 테스트를 회귀 유형별 regression ledger와 golden corpus로 묶는다.
2. 금액·비율·basis·VAT·정책 버전 ADR과 `contracts/`의 public/ML/event 계약을 작성한다.
3. Kotlin Gradle 모듈 골격, `Money`/`Rate` 값 객체, architecture test만 만들고 아직 route를 소유하지 않는다.
4. Python ML을 역할별로 분류하고 기존 `PricePredictionPort`에 DB/HTTP 예외 없는 canonical DTO 경계를 만든다.
5. React 독립 호스팅과 낮은 위험의 Kotlin read-only slice로 배포·관측·rollback 경로를 검증한다.

**Kotlin의 decision/bidding/settlement 구현은 1~5와 shadow exact comparison이 실제로 통과한 뒤** 시작한다. 이 순서라면 전환 중에도 계약, 테스트, ML 격리 개선이 현재 Python 시스템의 회귀를 먼저 줄인다.

## 12. 최종 판정

| 질문 | 판정 |
| --- | --- |
| React 분리는 적합한가 | 적합. 이미 번들 경계가 있어 가장 먼저 배포 분리 가능 |
| ML 학습을 Python에 두는가 | 적합 |
| ML 추론도 Python에 두는가 | 권장. 학습-serving 정합과 embedding 생태계 때문 |
| 서비스 API를 Kotlin으로 옮기는가 | 목표 구조로 적합. 단, 값 객체·도메인 모듈·회귀 게이트를 먼저 만들고 vertical slice로 수행 |
| 현재 코드를 한 번에 재작성하는가 | 부적합 |
| 처음부터 여러 Kotlin microservice로 나누는가 | 부적합. 모듈형 모놀리스로 시작 |
| DB를 계속 공동 write하는가 | 부적합 |
| 현재 제품 로드맵보다 우선하는가 | 부적합. Phase 3 검증을 막지 않는 architecture runway로 진행 |

따라서 승인할 계획은 **회귀 ledger·금전 불변식 고정 → 계약과 모듈 강제 → Python ML 역할 분리 → React 독립 배포 → Kotlin read/shadow slice → 금전 domain별 single-writer 전환 → 데이터 소유권 분리** 순서다. 언어 변경 완료가 아니라, 새 회귀가 해당 모듈 밖으로 전파되지 않고 과거 결정을 재현할 수 있을 때 완료로 본다.
