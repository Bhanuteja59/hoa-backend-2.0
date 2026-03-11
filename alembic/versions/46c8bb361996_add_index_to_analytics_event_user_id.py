"""add index to analytics_event user_id

Revision ID: 46c8bb361996
Revises: d3b8422e1aeb
Create Date: 2026-03-11 11:10:45.595379

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46c8bb361996'
down_revision: Union[str, None] = 'd3b8422e1aeb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_analytics_user_id", "analytics_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_analytics_user_id", table_name="analytics_events")
