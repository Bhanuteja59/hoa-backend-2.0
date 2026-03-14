"""Merge heads

Revision ID: ea72e57e5404
Revises: 48c0bfa4e90e, d13fbe236518
Create Date: 2026-03-14 15:21:40.247830

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea72e57e5404'
down_revision: Union[str, None] = ('48c0bfa4e90e', 'd13fbe236518')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
