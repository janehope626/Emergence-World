"""add society query indexes

Revision ID: 7d6e5f4a3b2c
Revises: 4a3c2d1e0f9b
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7d6e5f4a3b2c"
down_revision: str | None = "4a3c2d1e0f9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = (
    ("messages", "recipient_id"),
    ("messages", "sender_id"),
    ("messages", "world_id"),
    ("pitch_cycles", "world_id"),
    ("pitch_votes", "cycle_id"),
    ("pitch_votes", "pitch_id"),
    ("pitch_votes", "voter_id"),
    ("pitch_votes", "world_id"),
    ("pitches", "agent_id"),
    ("pitches", "cycle_id"),
    ("pitches", "world_id"),
    ("proposal_comments", "agent_id"),
    ("proposal_comments", "proposal_id"),
    ("proposal_comments", "world_id"),
    ("proposal_votes", "agent_id"),
    ("proposal_votes", "proposal_id"),
    ("proposal_votes", "world_id"),
    ("proposals", "proposer_id"),
    ("proposals", "world_id"),
    ("reaction_requests", "agent_id"),
    ("reaction_requests", "consumed"),
    ("reaction_requests", "world_id"),
)


def upgrade() -> None:
    for table, column in INDEXES:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column], unique=False)
    with op.batch_alter_table("constitution_articles") as batch_op:
        batch_op.drop_constraint(
            op.f("uq_constitution_articles_world_id"), type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_constitution_articles_world_position", ["world_id", "position"]
        )
        batch_op.create_unique_constraint(
            "uq_constitution_articles_world_title", ["world_id", "title"]
        )


def downgrade() -> None:
    with op.batch_alter_table("constitution_articles") as batch_op:
        batch_op.drop_constraint(
            "uq_constitution_articles_world_title", type_="unique"
        )
        batch_op.drop_constraint(
            "uq_constitution_articles_world_position", type_="unique"
        )
        batch_op.create_unique_constraint(
            op.f("uq_constitution_articles_world_id"), ["world_id", "position"]
        )
    for table, column in reversed(INDEXES):
        op.drop_index(op.f(f"ix_{table}_{column}"), table_name=table)
