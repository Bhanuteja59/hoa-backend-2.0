"""user_contacts

Revision ID: 0007_user_contacts
Revises: 0006_add_event_date
Create Date: 2024-02-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision = '0007_user_contacts'
down_revision = '0006_add_event_date'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_contacts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('relation', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('address', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_contacts_user', 'user_contacts', ['tenant_id', 'user_id'], unique=False)
    # Also index tenant_id for ForeignKey lookup performance? Model has index=True on tenant_id
    op.create_index(op.f('ix_user_contacts_tenant_id'), 'user_contacts', ['tenant_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_user_contacts_tenant_id'), table_name='user_contacts')
    op.drop_index('ix_user_contacts_user', table_name='user_contacts')
    op.drop_table('user_contacts')
