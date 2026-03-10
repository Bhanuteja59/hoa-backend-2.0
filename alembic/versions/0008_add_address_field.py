"""Add address field to TenantUser

Revision ID: 0008_add_address_field
Revises: 0007_user_contacts
Create Date: 2024-02-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0008_add_address_field'
down_revision = '0007_user_contacts'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenant_users', sa.Column('address', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('tenant_users', 'address')
