---
globs: ["service-api/**/*.kt", "service-api/**/*.kts"]
---

# Kotlin Service Rules

이 규칙은 `docs/design/python-kotlin-react-modularization-plan.md`의 회귀 방지 목표를
Kotlin 코드에 강제한다. Kotlin으로 옮겼다는 사실만으로 안전하다고 간주하지 않는다.

## 모듈 경계

- `service-api`는 Gradle 멀티모듈 모놀리스다. 업무 모듈은 `identity`, `operator`,
  `procurement`, `qualification`, `bidding`, `decision`, `settlement`, `notification`,
  `operations`, `ml-gateway`, `application` 경계를 따른다.
- 각 업무 모듈의 의존 방향은 `domain -> application -> adapter`다.
- `domain`은 Spring, JPA/jOOQ, Jackson, HTTP, RabbitMQ를 import하지 않는다.
- 다른 업무 모듈의 repository, DB record, `internal` 구현을 직접 참조하지 않는다.
  공개 command/query/event 계약과 식별자만 사용한다.
- `shared-kernel`에는 `Money`, `Rate`, `Basis`, 식별자, 시간 값 타입만 둔다.
  범용 `utils`, 공용 entity, 공용 repository를 추가하지 않는다.
- controller, scheduler, message consumer는 aggregate/repository를 직접 변경하지 않고
  application use case에 위임한다.

## 금액과 의사결정

- 확정 원화는 원 단위 `Long`, 계산 중 소수·세율은 `BigDecimal`을 사용한다.
  금액·비율 계산에 `Double`/`Float`를 사용하지 않는다.
- 금액에는 currency, basis, VAT treatment, provenance가 필요하다. 알 수 없는 값을
  `0`, `1.0`, 기본 enum으로 조용히 바꾸지 않는다.
- 비율의 내부 단위는 fraction 하나다. percent 변환은 adapter 경계에서 명시적으로 한다.
- 모든 반올림은 이름 있는 `RoundingPolicy`와 명시적인 `RoundingMode`를 사용한다.
- 법정 하한, 자격, 투찰, 정산 규칙은 효력일과 정책 버전을 가진다. 최종 결과에는
  input snapshot hash, policy version, model release id, actor/correlation id를 남긴다.
- Python ML 응답은 예측 입력일 뿐이다. ML이 최종 `bid_now/review/skip`, 자격, 하한,
  투찰 또는 정산 상태를 결정하거나 service table을 write하게 만들지 않는다.

## 영속화와 메시징

- aggregate별 writer는 하나다. Alembic과 Flyway가 같은 테이블을 관리하지 않는다.
- application dual write를 금지한다. 상태 전달은 transactional outbox와 idempotent
  inbox/consumer를 사용한다.
- command/event는 idempotency key, aggregate version, correlation/causation id를 가진다.
- 확정 금액 정정은 원본을 덮어쓰지 않고 정정 사유와 이전 값을 감사 가능하게 남긴다.
- domain model과 DB record/JSON DTO를 같은 클래스로 재사용하지 않는다.

## 테스트와 변경 규율

- 회귀를 먼저 재현하는 테스트를 만들고 최소 구현으로 통과시킨다.
- 금액·법정 하한·자격·정산 결과는 canonicalization 후 exact equality로 검증한다.
  tolerance는 ML 진단값에만 허용한다.
- domain unit/property test, 상태 전이 test, duplicate/out-of-order/redelivery test를
  변경 위험에 맞게 추가한다.
- architecture test로 domain framework import, 금지된 모듈 참조, 순환 의존을 막는다.
- 새 거대 파일이나 `common` 모듈로 기존 비대화를 복제하지 않는다. 분할만 하지 말고
  public API, fan-in/fan-out, 함수 책임도 함께 줄인다.
- 전역 Gradle이 아니라 저장소에 고정된 wrapper를 사용한다. 테스트나 정적 검사를
  skip하거나 baseline을 완화해 통과시키지 않는다.
