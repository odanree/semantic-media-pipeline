"""create eval_set table

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-04

Adds a manually-curated held-out evaluation set. Decoupled from `vote_events`
so that label collection (votes, cascades) and evaluation labels can't
contaminate each other when training a re-ranker.

A row pins one (search_query, file_path[, audio_segment_index]) tuple with
a binary label and an optional curator note. Re-pinning the same tuple
updates the existing row (ON CONFLICT in the router).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_set",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("search_query", sa.String(512), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("audio_segment_index", sa.Integer(), nullable=True),
        sa.Column("label", sa.Integer(), nullable=False),
        sa.Column("qdrant_point_id", UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("label IN (-1, 1)", name="ck_eval_set_label"),
    )
    op.create_index("idx_eval_query", "eval_set", ["search_query"])
    op.create_index("idx_eval_created", "eval_set", ["created_at"])
    # Unique per (query, file_path, segment); COALESCE so two NULL-segment pins
    # for the same query+path collapse to one row.
    op.execute(
        "CREATE UNIQUE INDEX uq_eval_query_path_seg "
        "ON eval_set (search_query, file_path, COALESCE(audio_segment_index, -1))"
    )


def downgrade() -> None:
    op.drop_index("uq_eval_query_path_seg", table_name="eval_set")
    op.drop_index("idx_eval_created", table_name="eval_set")
    op.drop_index("idx_eval_query", table_name="eval_set")
    op.drop_table("eval_set")
