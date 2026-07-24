"""add business verification fields to company_profiles

국세청 사업자등록번호 검증(상태조회/진위확인) 결과를 ``company_profiles`` 에
캡처하기 위한 네 컬럼을 추가한다. **원문 사업자번호는 저장하지 않는다**(설계 §비기능,
CLAUDE.md §8) — pepper HMAC-SHA256 해시만 저장하며 표시는 masked 번호만 사용한다:

* ``business_registration_number_hash`` — 정규화(숫자만) 번호의 HMAC-SHA256 해시.
  중복/검색 조회용으로 nullable + index(``ix_company_profiles_business_registration_number_hash``).
* ``business_verification_status`` — 검증 상태(unverified/verified/inactive/mismatch/
  unknown). ``server_default='unverified'`` 로 기존 행에 안전하게 적용된다.
* ``business_verification_payload`` — masked/normalized 상태 payload(원문·서비스키 미포함).
* ``business_verified_at`` — 검증 확인 시각(외부 조회가 실제로 수행된 경우에만 설정).

모두 additive nullable(상태는 server_default)이라 이미 채워진 테이블에 zero-data-loss
로 적용된다(기존 행: hash NULL, status='unverified', payload NULL, verified_at NULL).
SQLite(CI)/Postgres 양쪽에서 동작하며, 재적용 안전을 위해 존재 여부를 검사한다.

Revision ID: a1f4c8e7b2d9
Revises: d8b3f0a1c9e2
Create Date: 2026-07-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "a1f4c8e7b2d9"
down_revision: Union[str, None] = "d8b3f0a1c9e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "company_profiles"
HASH_COLUMN = "business_registration_number_hash"
STATUS_COLUMN = "business_verification_status"
PAYLOAD_COLUMN = "business_verification_payload"
VERIFIED_AT_COLUMN = "business_verified_at"
HASH_INDEX = "ix_company_profiles_business_registration_number_hash"


def _existing_columns(inspector) -> set[str]:
    return {col["name"] for col in inspector.get_columns(TABLE_NAME)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = _existing_columns(inspector)

    if HASH_COLUMN not in existing:
        op.add_column(
            TABLE_NAME,
            sa.Column(HASH_COLUMN, sa.String(length=64), nullable=True),
        )
    if STATUS_COLUMN not in existing:
        op.add_column(
            TABLE_NAME,
            sa.Column(
                STATUS_COLUMN,
                sa.String(length=20),
                nullable=False,
                server_default="unverified",
            ),
        )
    if PAYLOAD_COLUMN not in existing:
        op.add_column(
            TABLE_NAME,
            sa.Column(PAYLOAD_COLUMN, sa.Text(), nullable=True),
        )
    if VERIFIED_AT_COLUMN not in existing:
        op.add_column(
            TABLE_NAME,
            sa.Column(VERIFIED_AT_COLUMN, sa.DateTime(timezone=True), nullable=True),
        )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes(TABLE_NAME)}
    if HASH_INDEX not in existing_indexes:
        op.create_index(HASH_INDEX, TABLE_NAME, [HASH_COLUMN], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    existing_indexes = {ix["name"] for ix in inspector.get_indexes(TABLE_NAME)}
    if HASH_INDEX in existing_indexes:
        op.drop_index(HASH_INDEX, table_name=TABLE_NAME)

    existing = _existing_columns(inspector)
    for column in (VERIFIED_AT_COLUMN, PAYLOAD_COLUMN, STATUS_COLUMN, HASH_COLUMN):
        if column in existing:
            op.drop_column(TABLE_NAME, column)
