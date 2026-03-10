"""Add privacy settings fields to tenant_users

Revision ID: 0012
Revises: 0011_document_folders
Create Date: 2026-02-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_users", sa.Column("privacy_show_name", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("tenant_users", sa.Column("privacy_show_email", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("tenant_users", sa.Column("privacy_show_phone", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("tenant_users", sa.Column("privacy_show_address", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("tenant_users", sa.Column("directory_visibility", sa.String(length=20), nullable=False, server_default=sa.text("'RESIDENTS'")))


def downgrade() -> None:
    op.drop_column("tenant_users", "directory_visibility")
    op.drop_column("tenant_users", "privacy_show_address")
    op.drop_column("tenant_users", "privacy_show_phone")
    op.drop_column("tenant_users", "privacy_show_email")
    op.drop_column("tenant_users", "privacy_show_name")
