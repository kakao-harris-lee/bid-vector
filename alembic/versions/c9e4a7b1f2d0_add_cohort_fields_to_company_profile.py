"""add association_memberships and tech_fields to company_profiles

운영자의 cohort 정체성(협회 가입/기술부문)을 ``company_profiles`` 에 캡처하기 위한
두 다중값 텍스트 컬럼을 추가한다:

* ``association_memberships`` — 협회 가입 목록(예 "엔지니어링협회"). 콤마 구분 저장.
* ``tech_fields`` — 기술부문/전문분야 목록(TECH_FIELD_TERMS canonical). 콤마 구분 저장.

두 컬럼 모두 기존 ``license_codes``/``region_codes`` 와 같은 nullable ``Text`` 다중값
패턴이며, ``server_default=''`` 로 기존 행에 안전하게 적용된다(zero-data-loss ADD
COLUMN — 기존 행은 빈 문자열 = "미기재" 중립). SQLite(CI)/Postgres 양쪽에서 동작한다.

Revision ID: c9e4a7b1f2d0
Revises: f6b1d40a9c37
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9e4a7b1f2d0"
down_revision: Union[str, None] = "f6b1d40a9c37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_profiles",
        sa.Column(
            "association_memberships",
            sa.Text(),
            nullable=True,
            server_default="",
        ),
    )
    op.add_column(
        "company_profiles",
        sa.Column(
            "tech_fields",
            sa.Text(),
            nullable=True,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("company_profiles", "tech_fields")
    op.drop_column("company_profiles", "association_memberships")
