"""manual_add_user_preferences

Revision ID: 48c0bfa4e90e
Revises: 8469b921f350
Create Date: 2026-03-14 15:09:47.136255

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '48c0bfa4e90e'
down_revision: Union[str, None] = '8469b921f350'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
