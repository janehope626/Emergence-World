"""add autonomous turn audit

Revision ID: 4a3c2d1e0f9b
Revises: 9b4d2f6a7c81
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "4a3c2d1e0f9b"
down_revision: str | None = "9b4d2f6a7c81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("turns", sa.Column("stop_reason", sa.String(length=100)))
    op.create_table(
        "provider_interactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("world_id", sa.String(length=36), nullable=False),
        sa.Column("turn_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("raw_response_json", sa.JSON()),
        sa.Column("parsed_tool_calls_json", sa.JSON(), nullable=False),
        sa.Column("parse_error", sa.Text()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence_number >= 1", name=op.f("ck_provider_interactions_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["world_id", "turn_id"],
            ["turns.world_id", "turns.id"],
            name=op.f("fk_provider_interactions_world_id_turn_id_turns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["world_id"],
            ["worlds.id"],
            name=op.f("fk_provider_interactions_world_id_worlds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_interactions")),
        sa.UniqueConstraint(
            "turn_id", "sequence_number", name=op.f("uq_provider_interactions_turn_id")
        ),
    )
    op.create_index(
        op.f("ix_provider_interactions_turn_id"),
        "provider_interactions",
        ["turn_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_interactions_world_id"),
        "provider_interactions",
        ["world_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_provider_interactions_world_id"), table_name="provider_interactions"
    )
    op.drop_index(
        op.f("ix_provider_interactions_turn_id"), table_name="provider_interactions"
    )
    op.drop_table("provider_interactions")
    op.drop_column("turns", "stop_reason")
