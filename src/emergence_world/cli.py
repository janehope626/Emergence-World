"""Command-line interface for world initialization and inspection."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer
from alembic import command
from alembic.config import Config
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from emergence_world.db.models import (
    Agent,
    AgentState,
    Landmark,
    ProviderInteraction,
    ToolCall,
    ToolDefinition,
    Turn,
    World,
    WorldEvent,
)
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.seed import import_seed_bundle, load_seed_bundle
from emergence_world.metrics.awi import calculate_awi
from emergence_world.agents.models import AgentDecision, RequestedToolCall
from emergence_world.agents.providers.scripted import ScriptedProvider
from emergence_world.tools import ManualToolExecutor
from emergence_world.world.runtime import autonomous_step_world, step_world
from emergence_world.world.state import current_snapshot, replay_snapshot, snapshot_hash

app = typer.Typer(name="world", no_args_is_help=True)
console = Console()


def migrate_database(database: Path) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    command.upgrade(config, "head")


def session_factory_for(database: Path) -> sessionmaker[Session]:
    engine = create_sync_database_engine(sync_sqlite_url(database))
    return create_sync_session_factory(engine)


def resolve_world_id(session: Session, world_name: str | None = None) -> str:
    query = select(World)
    if world_name is not None:
        query = query.where(World.name == world_name)
    worlds = session.scalars(query.order_by(World.name)).all()
    if not worlds:
        raise typer.BadParameter("no initialized world found")
    if len(worlds) > 1:
        raise typer.BadParameter("multiple worlds found; provide --world")
    return str(worlds[0].id)


@app.command("init")
def init_world(
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
    random_seed: int = typer.Option(1, help="Recorded deterministic random seed."),
) -> None:
    """Migrate a database and import the versioned Season 1 seed bundle."""

    migrate_database(database)
    session_factory = session_factory_for(database)
    with sync_transaction(session_factory) as session:
        result = import_seed_bundle(
            session, load_seed_bundle(), random_seed=random_seed
        )
    action = "created" if result.created else "already exists"
    console.print(
        f"World {action}: {result.world_id} "
        f"({result.agents} agents, {result.landmarks} landmarks, "
        f"{result.tools} tools, {result.constitution_articles} articles)"
    )


@app.command("status")
def world_status(
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
) -> None:
    """Display initialized worlds and core record counts."""

    session_factory = session_factory_for(database)
    table = Table("World", "Status", "Agents", "Landmarks", "Tools")
    with session_factory() as session:
        tools = session.scalar(select(func.count()).select_from(ToolDefinition)) or 0
        for world in session.scalars(select(World).order_by(World.name)):
            agents = session.scalar(
                select(func.count())
                .select_from(Agent)
                .where(Agent.world_id == world.id)
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
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
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
        table.add_row(
            "Needs", f"E={state.energy:g} K={state.knowledge:g} I={state.influence:g}"
        )
        table.add_row("ComputeCredits", str(state.cached_credit_balance))
    console.print(table)


@app.command("inspect-landmark")
def inspect_landmark(
    name: str,
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
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
            "Gated Tools",
            ", ".join(landmark.metadata_json.get("gated_tools", [])) or "-",
        )
    console.print(table)


@app.command("list-tools")
def list_tools(
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
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
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
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
        table.add_row(
            "Argument Schema", json.dumps(tool.argument_schema, sort_keys=True)
        )
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
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
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


@app.command("step")
def step(
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
    world: str | None = typer.Option(
        None, help="World name when database has several."
    ),
    minutes: int = typer.Option(30, min=1, help="Simulated minutes advanced per turn."),
) -> None:
    """Schedule one deterministic turn and advance world mechanisms."""

    session_factory = session_factory_for(database)
    with sync_transaction(session_factory) as session:
        result = step_world(session, resolve_world_id(session, world), minutes)
    console.print_json(data=asdict(result))


@app.command("run")
def run(
    turns: int = typer.Option(..., min=1, help="Number of deterministic turns."),
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
    world: str | None = typer.Option(
        None, help="World name when database has several."
    ),
    minutes: int = typer.Option(30, min=1, help="Simulated minutes advanced per turn."),
) -> None:
    """Run a deterministic headless batch without an LLM."""

    session_factory = session_factory_for(database)
    last = None
    completed = 0
    with session_factory() as session:
        world_id = resolve_world_id(session, world)
    for _ in range(turns):
        try:
            with sync_transaction(session_factory) as session:
                last = step_world(session, world_id, minutes)
        except ValueError as exc:
            if str(exc) != "world has no live agents":
                raise
            break
        completed += 1
    with session_factory() as session:
        state_hash = snapshot_hash(current_snapshot(session, world_id))
    console.print_json(
        data={
            "turns": completed,
            "turns_requested": turns,
            "last_agent": last.agent_name if last is not None else None,
            "simulation_time": last.simulation_time if last is not None else None,
            "state_hash": state_hash,
        }
    )


def scripted_turn_provider(sequence: int) -> ScriptedProvider:
    return ScriptedProvider(
        [
            AgentDecision(
                tool_calls=(
                    RequestedToolCall(
                        call_id=f"scripted-{sequence}",
                        tool_name="go_to_place",
                        arguments={"place": "Central Plaza"},
                    ),
                )
            ),
            AgentDecision(terminate=True),
        ]
    )


async def run_one_autonomous_turn(
    session_factory: sessionmaker[Session],
    world_id: str,
    provider: ScriptedProvider,
    minutes: int,
) -> Any:
    with sync_transaction(session_factory) as session:
        return await autonomous_step_world(session, world_id, provider, minutes)


@app.command("run-autonomous")
def run_autonomous(
    turns: int = typer.Option(..., min=1, help="Number of autonomous turns."),
    provider: str = typer.Option("scripted", help="Decision provider."),
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
    world: str | None = typer.Option(
        None, help="World name when database has several."
    ),
    minutes: int = typer.Option(30, min=1, help="Simulated minutes advanced per turn."),
) -> None:
    """Run audited autonomous turns using the deterministic scripted provider."""

    if provider != "scripted":
        raise typer.BadParameter("only the scripted provider is currently supported")
    session_factory = session_factory_for(database)
    with session_factory() as session:
        world_id = resolve_world_id(session, world)
    completed = 0
    last = None
    for sequence in range(1, turns + 1):
        try:
            last = asyncio.run(
                run_one_autonomous_turn(
                    session_factory,
                    world_id,
                    scripted_turn_provider(sequence),
                    minutes,
                )
            )
        except ValueError as exc:
            if str(exc) != "world has no live agents":
                raise
            break
        completed += 1
    with session_factory() as session:
        state_hash = snapshot_hash(current_snapshot(session, world_id))
    console.print_json(
        data={
            "turns": completed,
            "turns_requested": turns,
            "last_turn_id": last.turn_id if last is not None else None,
            "last_agent": last.agent_name if last is not None else None,
            "last_stop_reason": last.stop_reason if last is not None else None,
            "state_hash": state_hash,
        }
    )


@app.command("inspect-turn")
def inspect_turn(
    turn_id: str,
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
) -> None:
    """Inspect one turn and its tool-call outcomes."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        turn = session.get(Turn, turn_id)
        if turn is None:
            raise typer.BadParameter(f"unknown turn: {turn_id}")
        calls = session.scalars(
            select(ToolCall)
            .where(ToolCall.turn_id == turn_id)
            .order_by(ToolCall.sequence_number)
        ).all()
        console.print_json(
            data={
                "id": turn.id,
                "world_id": turn.world_id,
                "agent_id": turn.agent_id,
                "sequence_number": turn.sequence_number,
                "turn_type": turn.turn_type.value,
                "status": turn.status.value,
                "tool_call_budget": turn.tool_call_budget,
                "tool_calls_used": turn.tool_calls_used,
                "provider": turn.provider,
                "model_name": turn.model_name,
                "stop_reason": turn.stop_reason,
                "context_version": turn.context_version,
                "context_hash": turn.context_hash,
                "tool_calls": [
                    {
                        "sequence_number": call.sequence_number,
                        "tool_name": call.tool_name,
                        "status": call.status.value,
                        "result": call.result_json,
                        "error": call.error,
                    }
                    for call in calls
                ],
            }
        )


@app.command("inspect-context")
def inspect_context(
    turn_id: str,
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
) -> None:
    """Inspect the immutable context supplied at the start of a turn."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        turn = session.get(Turn, turn_id)
        if turn is None:
            raise typer.BadParameter(f"unknown turn: {turn_id}")
        console.print_json(
            data={
                "context_version": turn.context_version,
                "context_hash": turn.context_hash,
                "context": turn.context_json,
            }
        )


@app.command("inspect-provider-responses")
def inspect_provider_responses(
    turn_id: str,
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
) -> None:
    """Inspect audited provider requests, raw responses, and parsed tool calls."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        interactions = session.scalars(
            select(ProviderInteraction)
            .where(ProviderInteraction.turn_id == turn_id)
            .order_by(ProviderInteraction.sequence_number)
        ).all()
        if not interactions and session.get(Turn, turn_id) is None:
            raise typer.BadParameter(f"unknown turn: {turn_id}")
        console.print_json(
            data=[
                {
                    "sequence_number": item.sequence_number,
                    "provider": item.provider,
                    "model_name": item.model_name,
                    "request": item.request_json,
                    "raw_response": item.raw_response_json,
                    "parsed_tool_calls": item.parsed_tool_calls_json,
                    "parse_error": item.parse_error,
                    "input_tokens": item.input_tokens,
                    "output_tokens": item.output_tokens,
                    "total_tokens": item.total_tokens,
                    "latency_ms": item.latency_ms,
                    "cost_usd": item.cost_usd,
                }
                for item in interactions
            ]
        )


@app.command("replay")
def replay(
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
    world: str | None = typer.Option(
        None, help="World name when database has several."
    ),
) -> None:
    """Replay the event log and verify it matches current projections."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        world_id = resolve_world_id(session, world)
        current_hash = snapshot_hash(current_snapshot(session, world_id))
        replayed_hash = snapshot_hash(replay_snapshot(session, world_id))
        event_count = (
            session.scalar(
                select(func.count())
                .select_from(WorldEvent)
                .where(WorldEvent.world_id == world_id)
            )
            or 0
        )
    matches = current_hash == replayed_hash
    console.print_json(
        data={
            "matches": matches,
            "events": event_count,
            "current_hash": current_hash,
            "replayed_hash": replayed_hash,
        }
    )
    if not matches:
        raise typer.Exit(code=1)


@app.command("metrics")
def metrics(
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
    world: str | None = typer.Option(
        None, help="World name when database has several."
    ),
) -> None:
    """Calculate observable AWI indicators and diagnostics."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        result = calculate_awi(session, resolve_world_id(session, world))
    console.print_json(data=result)


if __name__ == "__main__":
    app()
