"""add trace query indexes

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    indexes = {
        item["name"]
        for table in ("command_executions", "execution_spans")
        for item in inspect(op.get_bind()).get_indexes(table)
    }
    if "ix_command_executions_started_at" not in indexes:
        op.create_index(
            "ix_command_executions_started_at",
            "command_executions",
            ["started_at"],
        )
    if "ix_command_executions_world_started" not in indexes:
        op.create_index(
            "ix_command_executions_world_started",
            "command_executions",
            ["world_id", "started_at"],
        )
    if "ix_execution_spans_command_stage_status_sequence" not in indexes:
        op.create_index(
            "ix_execution_spans_command_stage_status_sequence",
            "execution_spans",
            ["command_id", "stage", "status", "sequence_number"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_spans_command_stage_status_sequence",
        table_name="execution_spans",
    )
    op.drop_index(
        "ix_command_executions_world_started",
        table_name="command_executions",
    )
    op.drop_index(
        "ix_command_executions_started_at",
        table_name="command_executions",
    )
