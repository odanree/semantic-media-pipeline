"""add details column to audit_logs

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-11

Adds a free-text `details` column to audit_logs so that structured
summaries (e.g. from the recovery agent) can be persisted alongside
the standard request metadata.

NOTE: This column was already applied to the live DB via a direct
ALTER TABLE before Alembic was introduced. Running `alembic stamp 0002`
marks this revision as applied without re-executing the DDL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("details", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "details")
