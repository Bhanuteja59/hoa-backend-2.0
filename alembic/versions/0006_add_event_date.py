"""add_event_date

Revision ID: 0006
Revises: 0005
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0006_add_event_date'
down_revision = '0005_add_payment_intents'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('announcements', sa.Column('event_date', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('announcements', 'event_date')
