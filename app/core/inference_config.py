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
    # 실행). 이제 겹침 자체는 lease 가 막지만(inference_jobs), 주기는 겹침이 아니라
    # **처리량**으로 유도한다. 세 값의 부등식이 근거다:
    #
    #   필요 처리량 = 활성 대상 / 스냅샷 최대 수명 = 5,708 / 6h ≈ 951건/시
    #   투입 처리량 = (3600/180) × 배치 100                  = 2,000건/시
    #   소진 처리량 = (3600/30) × outbox 배치 50             = 6,000건/시
    #
    #   필요(951) ≤ 투입(2,000) ≤ 소진(6,000)
    #
    # 아래쪽 여유(2.1배)는 수명 안에 한 회전을 돌기 위한 것이고, 위쪽 여유(3배)가
    # 이번 사고의 재발 방지선이다 — 투입이 소진을 넘으면 큐가 무한히 자란다(실제로
    # bid_vector_ml_inference 에 21,321건이 쌓였다). 60초 유지 시 투입 6,000건/시로
    # 소진과 정확히 같아져 여유가 0 이 된다.
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
    #   사고 실측(21,321건) 대비 2.3% 지점이므로 같은 사고가 재발하면 며칠이 아니라
    #   한 시간 안에 드러난다.
    ML_INFERENCE_QUEUE_DEPTH_WARN_THRESHOLD: int = 500
    NOTIFICATION_DELIVERY_OUTBOX_SCHEDULE_ENABLED: bool = False
    NOTIFICATION_DELIVERY_OUTBOX_INTERVAL_SECONDS: int = 30
    NOTIFICATION_DELIVERY_OUTBOX_BATCH_LIMIT: int = 50
    NOTIFICATION_DELIVERY_OUTBOX_LOCK_TIMEOUT_SECONDS: int = 600
