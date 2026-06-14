"""add society mechanisms

Revision ID: 68ed7b68e1c9
Revises: 1c7d31dbcb45
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "68ed7b68e1c9"
down_revision: Union[str, Sequence[str], None] = "1c7d31dbcb45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(36),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sender_id", sa.String(36), nullable=False),
        sa.Column("recipient_id", sa.String(36), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["world_id", "sender_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "recipient_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "reaction_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(36),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("source_event_id", sa.String(36), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("sequence_number >= 1", name="reaction_sequence_positive"),
        sa.UniqueConstraint("world_id", "sequence_number"),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agents.world_id", "agents.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(36),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proposer_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "accepted",
                "rejected",
                name="proposal_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("action_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["world_id", "proposer_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "proposal_votes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(36),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            sa.String(36),
            sa.ForeignKey("proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("choice", sa.String(10), nullable=False),
        sa.Column("implicit", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("choice IN ('for', 'against')", name="proposal_vote_choice"),
        sa.UniqueConstraint("proposal_id", "agent_id"),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "proposal_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(36),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            sa.String(36),
            sa.ForeignKey("proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "pitch_cycles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(36),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("world_id", "sequence_number"),
    )
    op.create_table(
        "pitches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(36),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cycle_id",
            sa.String(36),
            sa.ForeignKey("pitch_cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cycle_id", "agent_id"),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "pitch_votes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(36),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cycle_id",
            sa.String(36),
            sa.ForeignKey("pitch_cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pitch_id",
            sa.String(36),
            sa.ForeignKey("pitches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("voter_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cycle_id", "voter_id"),
        sa.ForeignKeyConstraint(
            ["world_id", "voter_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    for table in [
        "pitch_votes",
        "pitches",
        "pitch_cycles",
        "proposal_comments",
        "proposal_votes",
        "proposals",
        "reaction_requests",
        "messages",
    ]:
        op.drop_table(table)
