"""baseline — schema pre-exists via init-db.sql

Revision ID: 0001
Revises:
Create Date: 2026-04-11

Schema at this revision:
  - media_files      (init-db.sql + migrate_add_observability_columns.sql + migrate_add_model_version.sql)
  - audit_logs       (init-db.sql)
  - vote_events      (migrate_add_vote_events.sql)

This migration is intentionally empty — the database was created and
maintained by hand-written SQL scripts before Alembic was introduced.
Running `alembic stamp 0001` marks this revision as applied without
executing any DDL.
"""
from typing import Sequence, Union

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass  # DB already exists


def downgrade() -> None:
    pass  # cannot drop a pre-existing baseline
