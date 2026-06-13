"""Command-line interface for world initialization and inspection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from alembic import command
from alembic.config import Config
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from emergence_world.db.models import Agent, AgentState, Landmark, ToolDefinition, World
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.seed import import_seed_bundle, load_seed_bundle
from emergence_world.tools import ManualToolExecutor

app = typer.Typer(name="world", no_args_is_help=True)
console = Console()


def migrate_database(database: Path) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    command.upgrade(config, "head")


def session_factory_for(database: Path):
    engine = create_sync_database_engine(sync_sqlite_url(database))
    return create_sync_session_factory(engine)


@app.command("init")
def init_world(
    database: Path = typer.Option(Path("emergence_world.db"), help="SQLite database path."),
    random_seed: int = typer.Option(1, help="Recorded deterministic random seed."),
) -> None:
    """Migrate a database and import the versioned Season 1 seed bundle."""

    migrate_database(database)
    session_factory = session_factory_for(database)
    with sync_transaction(session_factory) as session:
        result = import_seed_bundle(session, load_seed_bundle(), random_seed=random_seed)
    action = "created" if result.created else "already exists"
    console.print(
        f"World {action}: {result.world_id} "
        f"({result.agents} agents, {result.landmarks} landmarks, "
        f"{result.tools} tools, {result.constitution_articles} articles)"
    )


@app.command("status")
def world_status(
    database: Path = typer.Option(Path("emergence_world.db"), help="SQLite database path."),
) -> None:
    """Display initialized worlds and core record counts."""

    session_factory = session_factory_for(database)
    table = Table("World", "Status", "Agents", "Landmarks", "Tools")
    with session_factory() as session:
        tools = session.scalar(select(func.count()).select_from(ToolDefinition)) or 0
        for world in session.scalars(select(World).order_by(World.name)):
            agents = session.scalar(
                select(func.count()).select_from(Agent).where(Agent.world_id == world.id)
            )
            landmarks = session.scalar(
                select(func.count())
                .select_from(Landmark)
                .where(Landmark.world_id == world.id)
            )
            table.add_row(
                world.name,
                world.status.value,
                str(agents or 0),
                str(landmarks or 0),
                str(tools),
            )
    console.print(table)


@app.command("inspect-agent")
def inspect_agent(
    name: str,
    database: Path = typer.Option(Path("emergence_world.db"), help="SQLite database path."),
) -> None:
    """Display one seeded agent profile and current state."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        row = session.execute(
            select(Agent, AgentState, Landmark)
            .join(AgentState, AgentState.agent_id == Agent.id)
            .join(Landmark, Landmark.id == AgentState.current_landmark_id)
            .where(Agent.name == name)
        ).one_or_none()
        if row is None:
            raise typer.BadParameter(f"unknown agent: {name}")
        agent, state, landmark = row
        table = Table("Field", "Value")
        table.add_row("Name", agent.name)
        table.add_row("Role", agent.role)
        table.add_row("North Star Goal", agent.north_star_goal)
        table.add_row("Location", landmark.name)
        table.add_row("Status", state.status.value)
        table.add_row("Needs", f"E={state.energy:g} K={state.knowledge:g} I={state.influence:g}")
        table.add_row("ComputeCredits", str(state.cached_credit_balance))
    console.print(table)


@app.command("inspect-landmark")
def inspect_landmark(
    name: str,
    database: Path = typer.Option(Path("emergence_world.db"), help="SQLite database path."),
) -> None:
    """Display a landmark and its location-gated tools."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        landmark = session.scalar(select(Landmark).where(Landmark.name == name))
        if landmark is None:
            raise typer.BadParameter(f"unknown landmark: {name}")
        table = Table("Field", "Value")
        table.add_row("Name", landmark.name)
        table.add_row("Category", landmark.category)
        table.add_row("Description", landmark.description)
        table.add_row("Open", str(landmark.is_open))
        table.add_row(
            "Gated Tools", ", ".join(landmark.metadata_json.get("gated_tools", [])) or "-"
        )
    console.print(table)


@app.command("list-tools")
def list_tools(
    database: Path = typer.Option(Path("emergence_world.db"), help="SQLite database path."),
) -> None:
    """List active versioned tools."""

    session_factory = session_factory_for(database)
    table = Table("Tool", "Version", "Locations")
    with session_factory() as session:
        for tool in session.scalars(
            select(ToolDefinition)
            .where(ToolDefinition.is_active.is_(True))
            .order_by(ToolDefinition.name)
        ):
            locations = tool.availability_rules.get("locations", [])
            table.add_row(tool.name, tool.version, ", ".join(locations) or "global")
    console.print(table)


@app.command("inspect-tool")
def inspect_tool(
    name: str,
    database: Path = typer.Option(Path("emergence_world.db"), help="SQLite database path."),
) -> None:
    """Display one tool definition and validation schema."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        tool = session.scalar(
            select(ToolDefinition).where(
                ToolDefinition.name == name, ToolDefinition.is_active.is_(True)
            )
        )
        if tool is None:
            raise typer.BadParameter(f"unknown tool: {name}")
        table = Table("Field", "Value")
        table.add_row("Name", tool.name)
        table.add_row("Version", tool.version)
        table.add_row("Description", tool.description)
        table.add_row("Argument Schema", json.dumps(tool.argument_schema, sort_keys=True))
        table.add_row(
            "Locations",
            ", ".join(tool.availability_rules.get("locations", [])) or "global",
        )
        table.add_row("Event Types", ", ".join(tool.produced_event_types) or "-")
    console.print(table)


@app.command("call-tool")
def call_tool(
    agent: str = typer.Argument(help="Agent name."),
    tool: str = typer.Argument(help="Tool name."),
    arguments: str = typer.Option("{}", "--arguments", "-a", help="JSON object."),
    database: Path = typer.Option(Path("emergence_world.db"), help="SQLite database path."),
) -> None:
    """Execute one validated and audited manual tool call."""

    try:
        parsed: Any = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("arguments must be a JSON object")

    executor = ManualToolExecutor(session_factory_for(database))
    result = executor.call(agent_name=agent, tool_name=tool, arguments=parsed)
    if result.success:
        console.print_json(data={"success": True, "result": result.result})
    else:
        console.print_json(data={"success": False, "error": result.error})
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
