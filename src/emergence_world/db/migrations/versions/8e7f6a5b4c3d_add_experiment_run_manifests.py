# 新增实验运行清单及可复现性元数据的数据库迁移。
"""add experiment run manifests

Revision ID: 8e7f6a5b4c3d
Revises: 7d6e5f4a3b2c
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "8e7f6a5b4c3d"
down_revision: str | None = "7d6e5f4a3b2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=200), nullable=False),
        sa.Column("experiment_id", sa.String(length=36), nullable=False),
        sa.Column("world_id", sa.String(length=36), nullable=False),
        sa.Column("git_commit", sa.String(length=64)),
        sa.Column("seed_version", sa.String(length=100)),
        sa.Column("seed_hash", sa.String(length=64)),
        sa.Column("config_hash", sa.String(length=64)),
        sa.Column("random_seed", sa.Integer(), nullable=False),
        sa.Column("initial_state_hash", sa.String(length=64)),
        sa.Column("context_builder_version", sa.String(length=100)),
        sa.Column("retrieval_policy_version", sa.String(length=100)),
        sa.Column("prompt_template_version", sa.String(length=100)),
        sa.Column("prompt_hash", sa.String(length=64)),
        sa.Column("tool_registry_hash", sa.String(length=64)),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("provider_model", sa.String(length=200)),
        sa.Column("provider_parameters_json", sa.JSON(), nullable=False),
        sa.Column("simulation_minutes_per_turn", sa.Integer(), nullable=False),
        sa.Column("max_turns", sa.Integer(), nullable=False),
        sa.Column("database_path", sa.Text()),
        sa.Column("dependency_lock_hash", sa.String(length=64)),
        sa.Column("environment_json", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "created",
                "running",
                "completed",
                "failed",
                name="experiment_run_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_experiment_runs_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["world_id"],
            ["worlds.id"],
            name=op.f("fk_experiment_runs_world_id_worlds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_runs")),
    )
    op.create_index(
        op.f("ix_experiment_runs_experiment_id"),
        "experiment_runs",
        ["experiment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_experiment_runs_run_id"),
        "experiment_runs",
        ["run_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_experiment_runs_world_id"),
        "experiment_runs",
        ["world_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_experiment_runs_world_id"), table_name="experiment_runs")
    op.drop_index(op.f("ix_experiment_runs_run_id"), table_name="experiment_runs")
    op.drop_index(
        op.f("ix_experiment_runs_experiment_id"), table_name="experiment_runs"
    )
    op.drop_table("experiment_runs")
