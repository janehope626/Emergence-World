"""Structured Seed Data validation and import tests."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from typer.testing import CliRunner

from emergence_world.cli import app
from emergence_world.db.models import (
    Agent,
    AgentState,
    ConstitutionArticle,
    Landmark,
    SeedDocument,
    SimulationClock,
    ToolDefinition,
    World,
)
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.seed import import_seed_bundle, load_seed_bundle


def migrated_session_factory(tmp_path: Path):
    database_path = tmp_path / "seed.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database_path))
    command.upgrade(config, "head")
    engine = create_sync_database_engine(sync_sqlite_url(database_path))
    return create_sync_session_factory(engine)


def test_seed_bundle_is_complete_and_explicit_about_assumptions() -> None:
    bundle = load_seed_bundle()

    assert len(bundle.agents) == 10
    assert len(bundle.landmarks) == 35
    assert len(bundle.tools) == 35
    assert len(bundle.constitution) == 5
    assert bundle.documents[0].document_type == "agent_manifesto"
    assert len(bundle.world.reproduction_assumptions) >= 5


def test_seed_import_creates_complete_world_and_is_idempotent(tmp_path: Path) -> None:
    session_factory = migrated_session_factory(tmp_path)
    bundle = load_seed_bundle()

    with sync_transaction(session_factory) as session:
        first = import_seed_bundle(session, bundle, random_seed=42)
    with sync_transaction(session_factory) as session:
        second = import_seed_bundle(session, bundle, random_seed=42)

    assert first.created is True
    assert second.created is False
    assert first.world_id == second.world_id
    assert first.agents == second.agents == 10
    assert first.landmarks == second.landmarks == 35
    assert first.tools == second.tools == 35
    assert first.constitution_articles == second.constitution_articles == 5

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(World)) == 1
        assert session.scalar(select(func.count()).select_from(Agent)) == 10
        assert session.scalar(select(func.count()).select_from(AgentState)) == 10
        assert session.scalar(select(func.count()).select_from(Landmark)) == 35
        assert session.scalar(select(func.count()).select_from(ToolDefinition)) == 35
        assert (
            session.scalar(select(func.count()).select_from(ConstitutionArticle)) == 5
        )
        assert session.scalar(select(func.count()).select_from(SeedDocument)) == 1
        assert session.scalar(select(func.count()).select_from(SimulationClock)) == 1


def test_seed_import_preserves_tool_gates_and_provenance(tmp_path: Path) -> None:
    session_factory = migrated_session_factory(tmp_path)
    bundle = load_seed_bundle()

    with sync_transaction(session_factory) as session:
        result = import_seed_bundle(session, bundle)

    with session_factory() as session:
        vote = session.scalar(
            select(ToolDefinition).where(ToolDefinition.name == "vote_on_proposal")
        )
        town_hall = session.scalar(
            select(Landmark).where(
                Landmark.world_id == result.world_id, Landmark.name == "Town Hall"
            )
        )
        world = session.get(World, result.world_id)
        manifesto = session.scalar(
            select(SeedDocument).where(
                SeedDocument.world_id == result.world_id,
                SeedDocument.document_type == "agent_manifesto",
            )
        )

    assert vote is not None
    assert vote.availability_rules["locations"] == ["Town Hall"]
    assert vote.availability_rules["schema_status"] == "reproduction_schema_v1"
    assert town_hall is not None
    assert town_hall.metadata_json["source_path"] == "docs/landmarks/README.md"
    assert world is not None
    assert world.config_json["reproduction_assumptions"]
    assert manifesto is not None
    assert len(manifesto.source_sha256) == 64


def test_cli_initializes_and_inspects_seeded_world(tmp_path: Path) -> None:
    database = tmp_path / "cli.db"
    runner = CliRunner()

    initialized = runner.invoke(app, ["init", "--database", str(database)])
    status = runner.invoke(app, ["status", "--database", str(database)])
    agent = runner.invoke(
        app, ["inspect-agent", "Anchor", "--database", str(database)]
    )

    assert initialized.exit_code == 0
    assert "10 agents" in initialized.stdout
    assert status.exit_code == 0
    assert "Season 1 Reproduction World" in status.stdout
    assert agent.exit_code == 0
    assert "Anchor" in agent.stdout
    assert "Central Plaza" in agent.stdout


def test_cli_inspects_and_calls_tools(tmp_path: Path) -> None:
    database = tmp_path / "tool-cli.db"
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--database", str(database)]).exit_code == 0

    landmark = runner.invoke(
        app, ["inspect-landmark", "Town Hall", "--database", str(database)]
    )
    tools = runner.invoke(app, ["list-tools", "--database", str(database)])
    tool = runner.invoke(
        app, ["inspect-tool", "go_to_place", "--database", str(database)]
    )
    call = runner.invoke(
        app,
        [
            "call-tool",
            "Anchor",
            "go_to_place",
            "--arguments",
            '{"place":"Town Hall"}',
            "--database",
            str(database),
        ],
    )

    assert landmark.exit_code == 0
    assert "vote_on_proposal" in landmark.stdout
    assert tools.exit_code == 0
    assert "inspect_location" in tools.stdout
    assert tool.exit_code == 0
    assert "agent_moved" in tool.stdout
    assert call.exit_code == 0
    assert '"to": "Town Hall"' in call.stdout
