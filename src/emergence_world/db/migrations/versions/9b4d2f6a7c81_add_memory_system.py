"""add memory system

Revision ID: 9b4d2f6a7c81
Revises: 68ed7b68e1c9
"""

from typing import Sequence, Union

from alembic import op

from emergence_world.db.base import Base
from emergence_world.db import models as _models  # noqa: F401

revision: str = "9b4d2f6a7c81"
down_revision: Union[str, Sequence[str], None] = "68ed7b68e1c9"
branch_labels = None
depends_on = None

TABLES = [
    "soul_entries",
    "soul_entry_revisions",
    "episodic_memories",
    "diary_entries",
    "diary_revisions",
    "conversation_records",
    "relationships",
    "relationship_revisions",
    "memory_summaries",
    "memory_summary_sources",
    "context_builds",
    "context_memory_candidates",
    "context_memory_selections",
]


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(bind=bind)
