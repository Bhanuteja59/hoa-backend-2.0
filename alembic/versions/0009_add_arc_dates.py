"""Add ARC dates

Revision ID: 0009_add_arc_dates
Revises: 0008_add_address_field
Create Date: 2026-02-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0009_add_arc_dates'
down_revision = '0008_add_address_field'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('arc_requests', sa.Column('estimated_start_date', sa.Date(), nullable=True))
    op.add_column('arc_requests', sa.Column('estimated_end_date', sa.Date(), nullable=True))
    op.add_column('arc_requests', sa.Column('actual_end_date', sa.Date(), nullable=True))


def downgrade():
    op.drop_column('arc_requests', 'actual_end_date')
    op.drop_column('arc_requests', 'estimated_end_date')
    op.drop_column('arc_requests', 'estimated_start_date')
