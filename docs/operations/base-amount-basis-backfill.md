# base_amount_basis 백필 런북

`scripts/backfill_base_amount_basis.py` 는 `historical_data.base_amount` 의 provenance
라벨(`clean` / `derived-yega` / `derived-vat` / `suspect-fractional` / `suspect-ratio`)을
다시 계산해 저장한다. **원본 금액(`base_amount`)은 절대 바꾸지 않는다** — 교정은 라벨과
`base_amount_estimated` 뿐이다(정직 명세 §2).

`clean` 은 밴드 캘리브레이션·백테스트·집계가 ground truth 로 신뢰하는 유일한 버킷이므로,
이 백필은 그 버킷의 내용물을 바꾸는 작업이다. 아래 순서를 지킨다.

## 1. dry-run (기본값, write 0)

```bash
docker exec bid_vector_api python scripts/backfill_base_amount_basis.py \
    --reclassify-clean --dry-run --audit /tmp/basis-dryrun.json
```

`--dry-run` 은 청크마다 `db.rollback()` 하고 어떤 속성도 대입하지 않는다. 스크립트를
격리 DB 로 돌리려고 `DATABASE_URL` 을 넘기는 것은 **동작하지 않는다** —
`app/core/config.py::_compose_database_url` 이 split `DATABASE_*` env 로 그 값을 덮어쓴다.
따라서 dry-run 도 운영 DB 를 읽으며, 실행 자체가 승인 대상이다(§0).

## 2. 출력 읽는 법

키 이름이 스코프를 말한다: `scan_*` 은 **스캔한 전체 행** 기준이고, `reclassified*` 는
**이동한 행** 기준이다.

| 출력 | 스코프 | 답하는 질문 |
|---|---|---|
| `scanned` / `reclassified` / `bucket_shrink_ratio` | — | 선택한 버킷이 얼마나 줄어드는가 |
| `scan_clean_remaining` | 스캔 | 실행 뒤 clean 으로 **몇 행**이 남는가(비율이 아니라 절대값 — 재캘리 게이트 입력) |
| `reclassified_by_status` / `_by_category` | 이동 | 투찰 가능 공고를 건드리는가, 어디에 몰렸는가 |
| `reclassified_with_reserve_estimate` | 이동 | 라벨만 바뀌는 행과 **하류 금액까지 바뀌는** 행의 구분 |
| `scan_estimated_filled_by_status` | 스캔 | 아래 §3 확인 항목의 입력 |
| `scan_est_equals_base` | 스캔 | 이 규칙이 **구조적으로 못 보는** 행 수(검증 커버리지) |
| `samples` | 이동 | 왜 움직이는가(두 금액 + 비율, 최대 12행 — 대표값이 아니라 예시다) |

> **분모 caveat.** 백필도 수집 경로와 **같은** `Project.budget_estimate` 를 읽는다. §6 대로
> settled 행은 그 값이 이미 예정가로 덮여 있을 수 있으므로, 그 코호트의 비율은 "base ÷
> 추정가격"이 아니라 "base ÷ 예정가"일 수 있다. 즉 dry-run 수치는 settled 구간에서
> **과소 계상** 쪽이다(분모가 크면 비율이 작아져 덜 잡힌다). 이는 PR 본문의 앵커 순환성
> 공시와 같은 뿌리이며, 어느 쪽이 파생값인지 가리지 않는다는 이 규칙의 주장 범위와도
> 일관된다.

## 3. apply 전 확인 항목 — 라이브 금액 영향

`get_reliable_base` 가 투찰 base **금액**을 바꾸는 경로는 하나뿐이다: non-clean 라벨 +
양수 `base_amount_estimated`. 따라서 `estimated_filled_by_status` 의 키 집합을
`ACTIVE_PROJECT_STATUSES`(`open`, `re_notice`)와 교차시킨다.

- 교집합이 비면 그 실행은 투찰 가능 공고의 금액을 바꾸지 않는다.
- 교집합이 있으면 **그 행들은 개별 확인 대상**이다. `open` 만 보면 안 된다 — `re_notice`
  도 투찰 가능 상태이고, 실제로 운영 실측이 `re_notice: 3` 을 냈다.

같은 행에서 #356 budget_cap 게이트의 판정도 함께 움직일 수 있다(게이트 입력 `bid_base` 가
`get_reliable_base` 를 거치므로). 방향은 CLOSED → OPEN 이며, 근거는
`enforceable_floor_price` docstring 에 있다.

또 하나: `--apply` 는 clean 으로 남는 행의 `base_amount_estimated` 를 `None` 으로 덮는다
(기존 동작). 되돌릴 값이 없으므로 apply 전에
`clean AND base_amount_estimated IS NOT NULL` 행수를 세어 둔다.

## 4. apply (사용자 승인 후)

```bash
docker exec bid_vector_api python scripts/backfill_base_amount_basis.py \
    --reclassify-clean --apply --audit /tmp/basis-apply.json
```

멱등하다: 두 번째 실행은 같은 버킷에서 이동 0 을 낸다. 청크마다 commit 하므로 중단 후
재개해도 이미 끝난 행을 다시 훑지 않는다.

## 5. 패스 선택

| 플래그 | 대상 |
|---|---|
| (없음) | `basis_checked_at` 이 비어 있는 행 — 첫 태깅 |
| `--recheck` | 스탬프된 행까지 전부 재분류 — **규칙이 바뀌었을 때 쓰는 패스** |
| `--reclassify-clean` | 저장 라벨이 `clean` 인 행만 재검사 |

이동 증적(샘플·분해)은 세 패스 모두에서 나온다. 계수 기준은 "저장 라벨과 달라졌는가"이므로
첫 태깅(`previous_basis` 없음)은 이동으로 세지 않는다.

## 6. 지속성 — 어떤 패스가 재태깅을 되돌리는가

수집 경로(`app/services/koneps/persistence.py::_update_historical_base_fields`)는 매 수집
주기마다 기존 행의 라벨을 **다시 계산해 덮어쓴다**. 그래서 지속성은 분류기가 읽는 분모
(`project.budget_estimate`)를 누가 덮을 수 있는지에 달려 있다 — `update_project_from_item`
이 태깅보다 **먼저** 그 분모를 쓰기 때문이다.

가드 이전에는 값이 양수이기만 하면 무조건 덮었고, 두 패스가 분모를 바꿔 재태깅을 되돌렸다
(scsbid 복귀 실측: base/추정가격 **1.16·1.20·1.24 복귀, 1.28·1.408 생존**). 지금은
`app/services/koneps/budget_fields.py` 의 **출처 인지 가드**가 그 자리를 지킨다: 공고 피드가
게시한 추정가격(`notice`)만 덮고, 개찰 파생 예정가(`derived`)·기초금액 폴백
(`base-fallback`)·미신고(`None`)는 **빈 자리(NULL/0)만** 채운다.

| 다음 패스 | 분모(`project.budget_estimate`) | 재태깅 |
|---|---|---|
| 공고 수집(추정가격 실림) | 추정가격 그대로(정정공고는 갱신) | **유지** |
| scsbid 개찰(6h) | 파생 예정가라 **덮지 못함** | **유지** |
| 추정가격 미공급 재수집 | 기초금액 폴백이라 **덮지 못함** | **유지** |

세 시퀀스는 `tests/test_koneps_persistence.py` 가 고정한다
(`test_scsbid_pass_no_longer_reverts_the_tag` /
`test_recollection_without_an_estimate_no_longer_reverts_the_tag` /
`test_notice_feed_still_applies_a_corrected_estimate`).

**남는 보상 통제:** `--reclassify-clean` 주기 재실행은 **낮은 강도로 유지**한다. 가드는
전망적 보호라, 가드 이전에 이미 분모가 덮인 행과 애초에 추정가격을 못 얻어 기초금액 사본이
저장된 행(§7 `est_equals_base`)은 그대로 남는다. 캘리브레이션·백테스트를 돌리기 **직전 한
번**이면 충분하고, scsbid 주기(6h)에 맞춰 돌릴 필요는 더 이상 없다.

## 7. 알려진 사각지대 / 후속

- **`est_equals_base`**: 수집이 추정가격을 못 얻으면 `matching.resolve_budget_estimate` 가
  `base_amount` 를 그대로 추정가격으로 쓴다. 비율이 항상 1.0 이라 규칙이 볼 수 없다
  (운영 실측 clean 의 30%). §6 의 가드는 그 사본이 **이미 저장된 추정가격을 덮는 것**만
  막는다 — 처음부터 사본이 자리를 채운 행은 그대로이므로, 수집 단계에서 추정가격을
  확보하는 것이 여전히 유일한 해법이다(활성 공고 실측 991건: open 362 + re_notice 629).
- **저측(base ÷ est < 0.85)**: 이 규칙의 축이 아니다. 별도 판정 후속.
- **프론트 표시**: `suspect-ratio` 행은 화면 provenance 가 `clean-base` →
  `base-fallback`("저장된 기초금액(basis 미상)")으로 후퇴한다. 모순으로 적극 판정한 값을
  "미상"이라 표시하는 것은 정직 명세의 역방향이라, `ReliableBaseSource` 에 suspect 전용
  source 를 추가하고 `frontend/src/shared/constants/amountBasis.ts` 에 라벨·경고를 붙이는
  후속이 필요하다(백엔드 열거형 확장이 선행).
