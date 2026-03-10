"""add_attachments

Revision ID: 0004_add_attachments
Revises: 0003_store_doc_content
Create Date: 2024-02-02 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0004_add_attachments'
down_revision = '0003_store_doc_content' # Linking to previous
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Add attachment_url to work_orders
    op.add_column('work_orders', sa.Column('attachment_url', sa.String(500), nullable=True))
    
    # Add attachment_url to violations
    op.add_column('violations', sa.Column('attachment_url', sa.String(500), nullable=True))
    
    # Add attachment_url to arc_requests
    op.add_column('arc_requests', sa.Column('attachment_url', sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column('work_orders', 'attachment_url')
    op.drop_column('violations', 'attachment_url')
    op.drop_column('arc_requests', 'attachment_url')
