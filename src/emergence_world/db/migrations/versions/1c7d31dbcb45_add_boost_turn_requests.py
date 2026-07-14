# 新增加速回合请求表及相关约束的数据库迁移。
"""add boost turn requests

Revision ID: 1c7d31dbcb45
Revises: f930b9403130
Create Date: 2026-06-14 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1c7d31dbcb45"
down_revision: Union[str, Sequence[str], None] = "f930b9403130"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "boost_turn_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("world_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "sequence_number >= 1",
            name=op.f("ck_boost_turn_requests_sequence_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agents.world_id", "agents.id"],
            name=op.f("fk_boost_turn_requests_world_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["world_id"],
            ["worlds.id"],
            name=op.f("fk_boost_turn_requests_world_id_worlds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boost_turn_requests")),
        sa.UniqueConstraint(
            "world_id",
            "sequence_number",
            name=op.f("uq_boost_turn_requests_world_id"),
        ),
    )
    op.create_index(
        op.f("ix_boost_turn_requests_agent_id"),
        "boost_turn_requests",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_boost_turn_requests_consumed"),
        "boost_turn_requests",
        ["consumed"],
        unique=False,
    )
    op.create_index(
        op.f("ix_boost_turn_requests_world_id"),
        "boost_turn_requests",
        ["world_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_boost_turn_requests_world_id"), table_name="boost_turn_requests"
    )
    op.drop_index(
        op.f("ix_boost_turn_requests_consumed"), table_name="boost_turn_requests"
    )
    op.drop_index(
        op.f("ix_boost_turn_requests_agent_id"), table_name="boost_turn_requests"
    )
    op.drop_table("boost_turn_requests")
