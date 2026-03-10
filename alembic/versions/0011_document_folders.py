"""Add document_folders table and folder_id to documents

Revision ID: 0011
Revises: 0010
Create Date: 2026-02-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010_move_phone_to_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create document_folders table
    op.create_table(
        "document_folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("document_folders.id"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "parent_id", "name", name="uq_folder_name_per_parent"),
    )
    op.create_index("ix_document_folders_tenant", "document_folders", ["tenant_id"])

    # Add folder_id to documents
    op.add_column(
        "documents",
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("document_folders.id"), nullable=True)
    )
    op.create_index("ix_documents_folder_id", "documents", ["folder_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_folder_id", table_name="documents")
    op.drop_column("documents", "folder_id")
    op.drop_index("ix_document_folders_tenant", table_name="document_folders")
    op.drop_table("document_folders")
