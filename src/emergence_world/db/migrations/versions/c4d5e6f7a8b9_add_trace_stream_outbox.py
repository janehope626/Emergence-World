"""add trace stream outbox

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
"""

from typing import Sequence, Union

from alembic import op

from emergence_world.db.base import Base
from emergence_world.db import models as _models  # noqa: F401

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["trace_stream_events"].create(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.tables["trace_stream_events"].drop(bind=op.get_bind())
