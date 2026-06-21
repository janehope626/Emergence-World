"""add execution tracing

Revision ID: a2b3c4d5e6f7
Revises: 8e7f6a5b4c3d
"""

from typing import Sequence, Union

from alembic import op

from emergence_world.db.base import Base
from emergence_world.db import models as _models  # noqa: F401

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "8e7f6a5b4c3d"
branch_labels = None
depends_on = None

TABLES = ["command_executions", "execution_spans", "state_diffs"]


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(bind=bind)
