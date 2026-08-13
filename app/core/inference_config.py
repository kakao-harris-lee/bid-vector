"""Configuration fields owned by durable inference delivery."""

from pydantic_settings import BaseSettings


class InferenceOutboxSettings(BaseSettings):
    APP_RELEASE_SHA: str = ""
    APP_RELEASE_TAG: str = ""
    INFERENCE_OUTBOX_SCHEDULE_ENABLED: bool = True
    INFERENCE_OUTBOX_INTERVAL_SECONDS: int = 30
    INFERENCE_OUTBOX_BATCH_LIMIT: int = 50
    INFERENCE_OUTBOX_LOCK_TIMEOUT_SECONDS: int = 600
    INFERENCE_OUTBOX_MAX_ATTEMPTS: int = 5
    INFERENCE_OUTBOX_RETRY_BASE_SECONDS: int = 5
    INFERENCE_OUTBOX_RESULT_SAMPLE_LIMIT: int = 50
    SIMILARITY_PROJECTION_BACKFILL_SCHEDULE_ENABLED: bool = True
    # 60초였고 근거가 없었다. 배치 실측이 55~80초였으므로 매 tick 이 직전 실행과
    # 겹쳤고, 그 겹침이 워커를 영구 점유했다(2026-08-13 사고: 4시간 동안 193회 연속
    # 실행). 이제 겹침 자체는 lease 가 막으므로(inference_jobs), 주기는 겹침이 아니라
    # **처리량**으로 유도한다.
    #
    # 코퍼스 실측 (2026-08-13, 임베딩 모델이 **두 벌**이라는 점이 핵심이다):
    #   models/sbert/...      service  코퍼스 3,593 · 임계 17.97행 · 활성 2,790
    #   (라이브)              construction 코퍼스 2,863 · 임계 14.32행 · 활성 1,812
    #   /app/models/sbert/... 코퍼스 68,827 · 활성 1,220 · **2026-08-04 이후 write 0**
    #   활성 합계 5,822
    #
    # 증가율은 **창을 명시해야** 한다. 하루치를 "일 증가율"로 쓰면 안 된다(초기 주석의
    # +695/+596 이 그 착오다 — 가장 최근 24시간 값이었고 7일 평균보다 40~60% 높다).
    # 재현 쿼리: embedding_model='models/sbert/…' AND category=? 로
    #   touched = count(*) where embedding_updated_at >= now - N일
    #   grown   = count(*) where embedding_updated_at is not null and created_at >= now - N일
    #
    #                  service              construction
    #   touched  7일   425.6/일             331.3/일
    #   touched  4일   523.0/일             423.5/일
    #   grown    7일   368.0/일             262.3/일
    #   단일 최대일    695(touched)/560(grown)  636/441
    #
    # drift 를 움직이는 것은 **touched 가 아니라 grown** 이다: 신선도 규칙은 저장된
    # count(embedding_updated_at) 과 현재값을 비교하는데, 기존 행의 재임베딩은 시각만
    # 바꾸고 그 count 를 바꾸지 않는다. touched 는 재임베딩까지 세므로 상한이다.
    #
    # 그래도 라이브 모델에서는 0.5% 임계가 18행/14행에 불과해 최대 수명(6h)보다 **먼저**
    # 구속한다: 17.97행 ÷ 15.3행/시(grown 7일) ≈ **70분**, touched 기준으로도 ≈61분,
    # 가장 바쁜 4일 창에서 ≈49분이다. 즉 무효화 동인은 수명이 아니라 drift 다.
    #
    # 그렇다고 필요 처리량이 무효화 횟수에 비례하지는 **않는다**. 한 대상은 한 회전에
    # 많아야 한 번 재계산되고, 그 한 번이 그동안 쌓인 drift 를 전부 지운다. 그래서
    # 작업량의 단위는 "무효화 이벤트"가 아니라 **서로 다른 대상**이고, 필요 처리량은
    # 우리가 보장하려는 노후도 상한 T 가 정한다:
    #
    #   회전 주기 = 활성 대상 / 투입 처리량 = 5,822 / 2,000 ≈ 2.9시간
    #   투입 처리량 = (3600/180) × 배치 100 = 2,000건/시
    #   소진 처리량 = (3600/30) × outbox 배치 50 = 6,000건/시
    #
    # 보장되는 것: **최대 수명 6h**. 회전 주기 2.9h < 6h 이므로 6h 를 넘긴 대상은 곧바로
    # 가장 오래된 축이 되어 다음 배치에 걸린다(엄밀히는 "항상 6h 미만"이 아니라 "6h 를
    # 넘긴 뒤 선택 지연 안에 해소").
    #
    # 그 지연이 분 단위라는 것은 **정상 상태 기준**이다. 스케줄을 며칠 끈 뒤 재개하면
    # 수천 건이 동시에 6h 를 넘겨 있으므로 지연이 시간 단위인 **과도구간**이 존재한다
    # (라이브 재생의 "최대 5.95h"가 바로 그 구간이다). 과도구간은 회전이 한 바퀴를
    # 돌면(활성/투입 ≈ 2.9h) 해소된다.
    #
    # 단 이 보장은 선택 순서가 **노후도 우선**일 때만 성립한다(_staleness_first_order).
    # id 순서면 회전 자체가 없어 보장이 아니라 기아가 된다. 라이브 상태(활성 5,822건의
    # 실제 id·스코프·현재 스냅샷 나이)를 24시간 재생한 결과:
    #
    #   노후도 우선: 미서빙 0건 · 첫 전수 순회 3.8h · 최대 5.95h · 6h 초과 0건
    #   id 순서    : **미서빙 2,502건** · 전수 순회 없음 · 최대 27.9h · 6h 초과 2,502건
    #
    # 보장되지 **않는** 것: 0.5% drift 임계. 라이브 모델 대상은 49~70분마다 임계를 넘는데
    # 재방문은 ~2.6시간마다이므로, 서빙되는 스냅샷이 임계의 두어 배(코퍼스의 1~2%)까지
    # 밀릴 수 있다.
    #
    # 이걸 상한으로 만드는 것이 **산술적으로 불가능하지는 않다** — 필요량은 창에 따라
    # 3,800~5,600건/시이고 소진 6,000건/시 아래다. 하지 않는 이유는 다른 데 있다:
    # 5,600건/시는 **소진 용량의 94%** 이고, 그러면 투입 ≈ 소진이 되어 아래 부등식의
    # 3배 여유가 정확히 사라진다. 그 여유가 이 PR 의 핵심 재발 방지선이므로, drift
    # 상한을 사려고 재발 방지선을 파는 거래는 하지 않는다. 여기서 보장하는 것은 수명뿐이다.
    #
    # 위쪽 부등식(투입 2,000 ≤ 소진 6,000, 3배)이 이번 사고의 재발 방지선이다. 투입이
    # 소진을 넘으면 큐가 무한히 자란다(실제로 21,321건). 60초 유지 시 투입이 6,000건/시로
    # 소진과 정확히 같아져 여유가 0 이 된다.
    #
    # 한계점: 수명 보장은 활성 대상이 투입×6h = **12,000건**을 넘으면 깨진다(현재
    # 5,822건, 2.1배 여유). 그 선을 넘으면 배치 크기를 올려야 하고, 그때도 투입이
    # 소진을 넘지 않도록 outbox 처리량을 함께 올려야 한다.
    #
    # 겹침 여유는 별개로 충분하다: P1 이후 배치 실측이 0.24초(limit=100, 질의
    # 0.22초 + 준비 판정 0.02초)라 180초는 그 750배다.
    SIMILARITY_PROJECTION_BACKFILL_INTERVAL_SECONDS: int = 180
    SIMILARITY_PROJECTION_BACKFILL_BATCH_LIMIT: int = 100
    # 단일 소비자 큐(bid_vector_ml_inference)의 깊이 경보선. 이 관측이 없어서
    # 21,321건이 며칠 쌓이는 동안 아무 신호도 없었다(2026-08-13). 기존 점검은 전부
    # 파이프라인이 **만든 행**을 보는데, 소비자가 막히면 볼 행 자체가 생기지 않는다.
    #
    # 500인 근거:
    #   정상 깊이 ≈ 0 — beat 가 넣는 것은 outbox 30초 + 백필 180초 = 2건/분이고
    #   메시지 1건 처리가 ~2초(outbox 배치 50 × 투영 ~30ms)라 큐가 비어 있는 것이
    #   정상 상태다. 500은 그보다 두 자릿수 위라 정상 운영에서는 울릴 수 없다.
    #   동시에 소비자 용량으로 500건은 약 5주기(180초) 분량이라, 잠깐의 수집 버스트가
    #   아니라 "소비자가 따라잡지 못하고 있다"에만 걸린다.
    #   사고 실측(21,321건) 대비 2.3% 지점이므로 같은 사고가 재발하면 훨씬 얕은 깊이에서
    #   걸린다.
    #
    # 다만 **검출까지의 지연은 임계가 아니라 스모크 주기가 정한다**: 스모크는
    # SMOKE_TEST_HOUR_KST 에 하루 한 번이라 최악의 경우 **24시간**이 걸린다. 즉 이
    # phase 가 바꾸는 것은 "며칠 동안 아무 신호 없음"에서 "하루 안에 드러남"이지
    # 실시간 경보가 아니다. 더 빠른 검출이 필요해지면 임계를 낮출 것이 아니라 이 점검을
    # 더 자주 도는 경로에 얹어야 한다.
    ML_INFERENCE_QUEUE_DEPTH_WARN_THRESHOLD: int = 500
    # 주기 sweep 태스크 메시지의 수명 = 주기 × 이 배수. 21,321건은 소비자가 막힌
    # 동안 beat 가 멈추지 않고 계속 밀어 넣은 결과다 — 소비자가 멈춰도 생산자는
    # 멈추지 않으므로, 백로그에 **구조적 상한**이 없으면 정지 시간에 비례해 무한히
    # 자란다. P4 의 큐 깊이는 그 상태를 *관측*할 뿐이고, 이 값이 *상한*이다.
    #
    # 3인 근거: 이 태스크들은 전부 idempotent sweep 이라 한 tick 을 놓쳐도 다음 tick 이
    # 같은 일을 한다. 따라서 오래된 메시지는 가치가 없고 버리는 것이 맞다. 1로 두면
    # 워커가 잠깐 바쁘기만 해도 정상 tick 이 버려져 처리량이 들쭉날쭉해지고, 크게 두면
    # 상한이 그만큼 느슨해진다. 3주기면 일시적 지연은 흡수하면서 스케줄당 미처리
    # 메시지를 3건 수준으로 묶는다(백필 180초 기준 9분, outbox 30초 기준 90초).
    PERIODIC_SWEEP_EXPIRY_INTERVAL_MULTIPLE: int = 3
    NOTIFICATION_DELIVERY_OUTBOX_SCHEDULE_ENABLED: bool = False
    NOTIFICATION_DELIVERY_OUTBOX_INTERVAL_SECONDS: int = 30
    NOTIFICATION_DELIVERY_OUTBOX_BATCH_LIMIT: int = 50
    NOTIFICATION_DELIVERY_OUTBOX_LOCK_TIMEOUT_SECONDS: int = 600
