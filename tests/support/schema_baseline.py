"""마이그레이션 이전(pre-migration) 베이스라인 스키마 재구성 — 단일 출처.

이 저장소는 스키마를 두 경로로 만든다: 모델 ``Base.metadata.create_all`` 과
alembic 마이그레이션. 마이그레이션은 과거 create_all 베이스라인 위의 **증분
패치**이므로, "마이그레이션이 실제로 만드는 것"을 검증하려면 그 베이스라인을
먼저 재구성해야 한다(모델 메타데이터 - 마이그레이션 소유 객체).

같은 재구성을 두 가드가 쓴다.

* ``tests/test_schema_drift.py`` — SQLite, (table, column) **이름** 수준 비교.
* ``tests/test_postgres_alembic.py`` — 실제 Postgres, **타입** 수준 비교 + 적용 스모크.

두 가드가 서로 다른 베이스라인을 보면 비교 자체가 의미를 잃으므로 재구성 로직과
등록부(어떤 테이블/컬럼이 마이그레이션 소유인가)를 여기 한 벌만 둔다.

유지보수
--------
NEW 마이그레이션이 테이블을 만들거나 컬럼을 추가하면 아래
:data:`MIGRATION_OWNED_TABLES` / :data:`MIGRATION_ADDED_COLUMNS` 에 등록한다.
빠뜨리면 두 가드가 시끄럽게 실패하는데, 그것이 의도된 동작이다 — 모델과
마이그레이션을 강제로 같이 움직이게 한다.
"""

from __future__ import annotations

from sqlalchemy import Engine, MetaData, Table

from app.core.database import Base

# Tables created by an alembic migration (not part of the historical baseline).
MIGRATION_OWNED_TABLES = {
    "synthetic_experiments",
    "synthetic_experiment_runs",
    "synthetic_experiment_results",
    "smoke_test_runs",
    "operator_notification_channels",
    "onboarding_suggestions",
    "operator_preview_snapshots",
    "project_similarity_snapshots",
    "project_similarity_edges",
    "inference_outbox_events",
    "similar_projects_refresh_operations",
    "operator_strategy_run_items",
    "notification_delivery_outbox",
    "tender_result_events",
}

# Columns added to a pre-existing table by an alembic migration.
MIGRATION_ADDED_COLUMNS = {
    "projects": {"business_type_code", "business_type_label"},
    "synthetic_experiment_results": {"breakdown_json"},
    "company_profiles": {
        "construction_capacity_amount",
        "awarded_contract_limit",
        "association_memberships",
        "tech_fields",
    },
    "crawl_jobs": {
        "celery_task_id",
        "category",
        "execution_mode",
        "max_items",
        "received_count",
        "normalized_count",
        "duplicate_count",
        "dropped_count",
        "persisted_count",
        "source_total_count",
        "pages_fetched",
        "truncated",
        "drop_reasons",
        "release_sha",
        "release_tag",
    },
    "operator_strategy_runs": {
        "projection_not_ready_count",
        "release_sha",
        "release_tag",
    },
    "bid_decision_records": {"monitor_run_id"},
    "notifications": {"monitor_run_id", "project_id", "decision_record_id"},
    "paper_bid_settlements": {"estimated_price", "minimum_bid_price"},
    "tender_results": {
        "opening_rank1_company",
        "opening_rank1_business_no",
        "opening_rank1_amount",
        "opening_rank1_rate",
        "opening_participant_count",
        "opened_at",
        "opening_checked_at",
        "is_current",
    },
}

ALEMBIC_INTERNAL_TABLES = {"alembic_version"}


def create_premigration_baseline(engine: Engine) -> None:
    """마이그레이션 이전 스키마(모델 - 마이그레이션 소유 객체)를 만든다.

    ``alembic upgrade head`` 가 create_all 이 아니라 **마이그레이션 코드로** 그
    객체들을 다시 만들게 하려는 것이다. 컬럼만 복사하므로 마이그레이션이 추가한
    컬럼을 참조하는 테이블 수준 제약/인덱스는 베이스라인에서 빠진다.

    전제 (Postgres 에서만 드러남)
    ----------------------------
    ``Column._copy()`` 는 컬럼에 달린 외래키를 함께 복사한다. 따라서 베이스라인에
    남는 테이블이 :data:`MIGRATION_OWNED_TABLES` 의 테이블을 FK 로 참조하게 되면,
    참조 대상이 아직 없는 상태에서 ``create_all`` 이 돌아 Postgres 는
    ``UndefinedTable`` 로 죽는다(SQLite 는 FK 를 느슨하게 처리해 통과시키므로
    이름 수준 가드만 돌리면 알 수 없다). 2026-08-05 실측 기준 그런 참조는 없고,
    새로 생기면 Postgres 가드가 먼저 실패한다 — 그때는 그 FK 도 마이그레이션
    소유로 다루거나 베이스라인에서 함께 제외해야 한다.
    """
    baseline = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name in MIGRATION_OWNED_TABLES:
            continue
        dropped = MIGRATION_ADDED_COLUMNS.get(table.name, set())
        columns = [
            column._copy() for column in table.columns if column.name not in dropped
        ]
        Table(table.name, baseline, *columns)
    baseline.create_all(bind=engine)
