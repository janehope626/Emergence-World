# 定义世界初始化、运行、检查、追踪、回放和指标计算等命令行入口。
"""Command-line interface for world initialization and inspection."""

from __future__ import annotations

import asyncio
import json
from os import environ
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    CommandExecution,
    ExecutionSpan,
    Experiment,
    ExperimentRun,
    Landmark,
    ProviderInteraction,
    SimulationClock,
    StateDiff,
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
from emergence_world.db.base import utc_now
from emergence_world.db.types import ExperimentRunStatus, TurnStatus
from emergence_world.experiments import create_experiment_run
from emergence_world.experiments.readiness import readiness_report
from emergence_world.seed import import_seed_bundle, load_seed_bundle
from emergence_world.metrics.awi import calculate_awi
from emergence_world.observability.query import (
    delete_traces,
    list_trace_summaries,
    trace_prune_candidates,
)
from emergence_world.agents.models import AgentDecision, RequestedToolCall
from emergence_world.agents.providers.base import AgentProvider
from emergence_world.agents.providers.doubao import DoubaoProvider, DoubaoProviderConfig
from emergence_world.agents.providers.openai import OpenAIProvider, OpenAIProviderConfig
from emergence_world.agents.providers.scripted import ScriptedProvider
from emergence_world.agents.providers.smoke import ProviderFailure, ProviderSmokeConfig
from emergence_world.tools import ManualToolExecutor
from emergence_world.world.runtime import autonomous_step_world, step_world
from emergence_world.world.state import current_snapshot, replay_snapshot, snapshot_hash

app = typer.Typer(name="world", no_args_is_help=True)
console = Console()


@app.command("serve")
def serve(
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8000, min=1, max=65535),
    cors_origins: str = typer.Option(
        "", help="Comma-separated browser origins; empty disables CORS."
    ),
) -> None:
    """Run the single-worker REST and WebSocket observability service."""

    import uvicorn

    from emergence_world.api import create_app

    origins = tuple(item.strip() for item in cors_origins.split(",") if item.strip())
    api = create_app(
        database,
        payload_access_token=environ.get("EMERGENCE_TRACE_PAYLOAD_TOKEN"),
        cors_origins=origins,
    )
    uvicorn.run(api, host=host, port=port, workers=1)


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
        result = step_world(
            session,
            resolve_world_id(session, world),
            minutes,
            command_name="step",
        )
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
                last = step_world(session, world_id, minutes, command_name="run")
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


def scripted_provider_metadata(
    smoke_config: ProviderSmokeConfig | None = None,
) -> tuple[str, str, dict[str, Any]]:
    effective_config = smoke_config or ProviderSmokeConfig()
    return (
        ScriptedProvider.provider_name,
        ScriptedProvider.model_name,
        {
            "strategy": "go_to_central_plaza_then_terminate",
            "smoke_config": effective_config.model_dump(mode="json"),
        },
    )


def provider_smoke_config(
    *,
    turns: int,
    max_provider_calls_per_turn: int,
    max_tool_calls_per_turn: int,
    max_input_tokens_per_request: int,
    max_output_tokens_per_request: int,
    max_total_cost_usd: float,
    timeout_seconds: float,
    max_retries: int,
) -> ProviderSmokeConfig:
    return ProviderSmokeConfig(
        max_turns=turns,
        max_provider_calls_per_turn=max_provider_calls_per_turn,
        max_tool_calls_per_turn=max_tool_calls_per_turn,
        max_input_tokens_per_request=max_input_tokens_per_request,
        max_output_tokens_per_request=max_output_tokens_per_request,
        max_total_cost_usd=max_total_cost_usd,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def openai_provider_config(
    *,
    model: str | None,
    smoke_config: ProviderSmokeConfig,
    input_cost_per_million_tokens_usd: float | None,
    output_cost_per_million_tokens_usd: float | None,
) -> OpenAIProviderConfig:
    if not model:
        raise typer.BadParameter("--model is required for the openai provider")
    if input_cost_per_million_tokens_usd is None:
        raise typer.BadParameter(
            "--input-cost-per-million-tokens-usd is required for the openai provider"
        )
    if output_cost_per_million_tokens_usd is None:
        raise typer.BadParameter(
            "--output-cost-per-million-tokens-usd is required for the openai provider"
        )
    return OpenAIProviderConfig(
        model=model,
        smoke_config=smoke_config,
        input_cost_per_million_tokens_usd=input_cost_per_million_tokens_usd,
        output_cost_per_million_tokens_usd=output_cost_per_million_tokens_usd,
    )


def doubao_provider_config(
    *,
    model: str | None,
    smoke_config: ProviderSmokeConfig,
    input_cost_per_million_tokens_usd: float | None,
    output_cost_per_million_tokens_usd: float | None,
) -> DoubaoProviderConfig:
    if not model:
        raise typer.BadParameter("--model is required for the doubao provider")
    if input_cost_per_million_tokens_usd is None:
        raise typer.BadParameter(
            "--input-cost-per-million-tokens-usd is required for the doubao provider"
        )
    if output_cost_per_million_tokens_usd is None:
        raise typer.BadParameter(
            "--output-cost-per-million-tokens-usd is required for the doubao provider"
        )
    return DoubaoProviderConfig(
        model=model,
        smoke_config=smoke_config,
        input_cost_per_million_tokens_usd=input_cost_per_million_tokens_usd,
        output_cost_per_million_tokens_usd=output_cost_per_million_tokens_usd,
    )


def experiment_run_view(run: ExperimentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_id": run.run_id,
        "experiment_id": run.experiment_id,
        "world_id": run.world_id,
        "git_commit": run.git_commit,
        "seed_version": run.seed_version,
        "seed_hash": run.seed_hash,
        "config_hash": run.config_hash,
        "random_seed": run.random_seed,
        "initial_state_hash": run.initial_state_hash,
        "context_builder_version": run.context_builder_version,
        "retrieval_policy_version": run.retrieval_policy_version,
        "prompt_template_version": run.prompt_template_version,
        "prompt_hash": run.prompt_hash,
        "tool_registry_hash": run.tool_registry_hash,
        "provider_name": run.provider_name,
        "provider_model": run.provider_model,
        "provider_parameters_json": run.provider_parameters_json,
        "simulation_minutes_per_turn": run.simulation_minutes_per_turn,
        "max_turns": run.max_turns,
        "database_path": run.database_path,
        "dependency_lock_hash": run.dependency_lock_hash,
        "environment_json": run.environment_json,
        "status": run.status.value,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


@app.command("create-run")
def create_run(
    run_id: str = typer.Option(..., help="Unique experiment run identifier."),
    provider: str = typer.Option("scripted", help="Decision provider."),
    turns: int = typer.Option(..., min=1, help="Maximum autonomous turns."),
    random_seed: int = typer.Option(1, help="Recorded deterministic random seed."),
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
    world: str | None = typer.Option(
        None, help="World name when database has several."
    ),
    minutes: int = typer.Option(30, min=1, help="Simulated minutes per turn."),
    mode: str = typer.Option(
        "development", help="Experiment mode: development or formal."
    ),
    model: str | None = typer.Option(None, help="Provider model identifier."),
    input_cost_per_million_tokens_usd: float | None = typer.Option(
        None, min=0, help="Explicit input token price used for budget enforcement."
    ),
    output_cost_per_million_tokens_usd: float | None = typer.Option(
        None, min=0, help="Explicit output token price used for budget enforcement."
    ),
) -> None:
    """Create an immutable experiment run manifest without starting the run."""

    if provider not in {"scripted", "openai", "doubao"}:
        raise typer.BadParameter("provider must be scripted, openai, or doubao")
    if mode not in {"development", "formal"}:
        raise typer.BadParameter("mode must be development or formal")
    migrate_database(database)
    session_factory = session_factory_for(database)
    if provider == "scripted":
        smoke_config = ProviderSmokeConfig(max_turns=turns)
        provider_name, provider_model, provider_parameters = scripted_provider_metadata(
            smoke_config
        )
    elif provider == "openai":
        smoke_config = ProviderSmokeConfig(
            max_turns=turns,
            max_provider_calls_per_turn=2,
            max_tool_calls_per_turn=1,
            max_output_tokens_per_request=1_000,
            max_total_cost_usd=0.25,
            max_retries=1,
        )
        openai_config = openai_provider_config(
            model=model,
            smoke_config=smoke_config,
            input_cost_per_million_tokens_usd=input_cost_per_million_tokens_usd,
            output_cost_per_million_tokens_usd=output_cost_per_million_tokens_usd,
        )
        provider_name, provider_model, provider_parameters = (
            OpenAIProvider.provider_name,
            openai_config.model,
            openai_config.manifest_parameters(),
        )
    else:
        smoke_config = ProviderSmokeConfig(
            max_turns=turns,
            max_provider_calls_per_turn=2,
            max_tool_calls_per_turn=1,
            max_output_tokens_per_request=1_000,
            max_total_cost_usd=0.25,
            max_retries=1,
        )
        doubao_config = doubao_provider_config(
            model=model,
            smoke_config=smoke_config,
            input_cost_per_million_tokens_usd=input_cost_per_million_tokens_usd,
            output_cost_per_million_tokens_usd=output_cost_per_million_tokens_usd,
        )
        provider_name, provider_model, provider_parameters = (
            DoubaoProvider.provider_name,
            doubao_config.model,
            doubao_config.manifest_parameters(),
        )
    try:
        with sync_transaction(session_factory) as session:
            run = create_experiment_run(
                session,
                run_id=run_id,
                world_id=resolve_world_id(session, world),
                provider_name=provider_name,
                provider_model=provider_model,
                provider_parameters=provider_parameters,
                random_seed=random_seed,
                simulation_minutes_per_turn=minutes,
                max_turns=turns,
                database_path=database,
                experiment_mode=mode,
            )
            output = experiment_run_view(run)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(data=output)


@app.command("inspect-run")
def inspect_run(
    run_id: str,
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
) -> None:
    """Inspect one persisted experiment run manifest."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        run = session.scalar(select(ExperimentRun).where(ExperimentRun.run_id == run_id))
        if run is None:
            raise typer.BadParameter(f"unknown run: {run_id}")
        output = experiment_run_view(run)
    console.print_json(data=output)


async def run_one_autonomous_turn(
    session_factory: sessionmaker[Session],
    world_id: str,
    provider: AgentProvider,
    minutes: int,
) -> Any:
    session = session_factory()
    try:
        result = await autonomous_step_world(
            session,
            world_id,
            provider,
            minutes,
            command_name="run-autonomous",
        )
        session.commit()
        return result
    except ProviderFailure as failure:
        turn = session.scalar(
            select(Turn)
            .where(Turn.world_id == world_id, Turn.status == TurnStatus.RUNNING)
            .order_by(Turn.sequence_number.desc())
        )
        clock = session.get(SimulationClock, world_id)
        if turn is not None:
            turn.status = TurnStatus.FAILED
            turn.stop_reason = f"provider_failure:{failure.code.value}"
            turn.ended_at = clock.current_time if clock is not None else utc_now()
        session.commit()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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
    run_id: str | None = typer.Option(
        None, help="Unique experiment run identifier; generated when omitted."
    ),
    random_seed: int | None = typer.Option(
        None, help="Recorded random seed; defaults to the initialized experiment seed."
    ),
    mode: str = typer.Option(
        "development", help="Experiment mode: development or formal."
    ),
    model: str | None = typer.Option(None, help="Provider model identifier."),
    allow_external_provider: bool = typer.Option(
        False,
        "--allow-external-provider",
        help="Explicitly permit requests to a real provider.",
    ),
    max_provider_calls_per_turn: int = typer.Option(2, min=1),
    max_tool_calls_per_turn: int = typer.Option(1, min=1),
    max_input_tokens_per_request: int = typer.Option(32_000, min=1),
    max_output_tokens_per_request: int = typer.Option(1_000, min=1),
    max_total_cost_usd: float = typer.Option(0.25, min=0),
    timeout_seconds: float = typer.Option(60.0, min=0.1),
    max_retries: int = typer.Option(1, min=0),
    input_cost_per_million_tokens_usd: float | None = typer.Option(None, min=0),
    output_cost_per_million_tokens_usd: float | None = typer.Option(None, min=0),
) -> None:
    """Run audited autonomous turns using an explicitly selected provider."""

    if provider not in {"scripted", "openai", "doubao"}:
        raise typer.BadParameter("provider must be scripted, openai, or doubao")
    if provider in {"openai", "doubao"} and not allow_external_provider:
        raise typer.BadParameter(
            f"{provider} provider requires explicit --allow-external-provider"
        )
    if mode not in {"development", "formal"}:
        raise typer.BadParameter("mode must be development or formal")
    migrate_database(database)
    session_factory = session_factory_for(database)
    smoke_config = provider_smoke_config(
        turns=turns,
        max_provider_calls_per_turn=max_provider_calls_per_turn,
        max_tool_calls_per_turn=max_tool_calls_per_turn,
        max_input_tokens_per_request=max_input_tokens_per_request,
        max_output_tokens_per_request=max_output_tokens_per_request,
        max_total_cost_usd=max_total_cost_usd,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
    real_provider: AgentProvider | None = None
    if provider == "scripted":
        provider_name, provider_model, provider_parameters = scripted_provider_metadata(
            smoke_config
        )
    elif provider == "openai":
        openai_config = openai_provider_config(
            model=model,
            smoke_config=smoke_config,
            input_cost_per_million_tokens_usd=input_cost_per_million_tokens_usd,
            output_cost_per_million_tokens_usd=output_cost_per_million_tokens_usd,
        )
        real_provider = OpenAIProvider(openai_config)
        provider_name, provider_model, provider_parameters = (
            real_provider.provider_name,
            real_provider.model_name,
            openai_config.manifest_parameters(),
        )
    else:
        doubao_config = doubao_provider_config(
            model=model,
            smoke_config=smoke_config,
            input_cost_per_million_tokens_usd=input_cost_per_million_tokens_usd,
            output_cost_per_million_tokens_usd=output_cost_per_million_tokens_usd,
        )
        try:
            real_provider = DoubaoProvider(doubao_config)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        provider_name, provider_model, provider_parameters = (
            real_provider.provider_name,
            real_provider.model_name,
            doubao_config.manifest_parameters(),
        )
    effective_run_id = run_id or f"autonomous-{uuid4()}"
    try:
        with sync_transaction(session_factory) as session:
            world_id = resolve_world_id(session, world)
            current_world = session.get(World, world_id)
            assert current_world is not None
            experiment = session.get(Experiment, current_world.experiment_id)
            assert experiment is not None
            effective_seed = (
                random_seed if random_seed is not None else experiment.random_seed
            )
            created_run = create_experiment_run(
                session,
                run_id=effective_run_id,
                world_id=world_id,
                provider_name=provider_name,
                provider_model=provider_model,
                provider_parameters=provider_parameters,
                random_seed=effective_seed,
                simulation_minutes_per_turn=minutes,
                max_turns=turns,
                database_path=database,
                status=ExperimentRunStatus.RUNNING,
                experiment_mode=mode,
            )
            experiment_run_id = created_run.id
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    completed = 0
    last = None
    try:
        for sequence in range(1, turns + 1):
            try:
                last = asyncio.run(
                    run_one_autonomous_turn(
                        session_factory,
                        world_id,
                        real_provider or scripted_turn_provider(sequence),
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
        with sync_transaction(session_factory) as session:
            completed_run = session.get(ExperimentRun, experiment_run_id)
            assert completed_run is not None
            completed_run.status = ExperimentRunStatus.COMPLETED
            completed_run.completed_at = utc_now()
    except Exception:
        with sync_transaction(session_factory) as session:
            failed_run = session.get(ExperimentRun, experiment_run_id)
            assert failed_run is not None
            failed_run.status = ExperimentRunStatus.FAILED
            failed_run.completed_at = utc_now()
        raise
    console.print_json(
        data={
            "run_id": effective_run_id,
            "turns": completed,
            "turns_requested": turns,
            "last_turn_id": last.turn_id if last is not None else None,
            "last_agent": last.agent_name if last is not None else None,
            "last_stop_reason": last.stop_reason if last is not None else None,
            "state_hash": state_hash,
        }
    )


@app.command("demo-trace")
def demo_trace(
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
    world: str | None = typer.Option(
        None, help="World name when database has several."
    ),
    minutes: int = typer.Option(30, min=1, help="Simulated minutes advanced."),
) -> None:
    """Create one safe scripted trace for the observability UI."""

    migrate_database(database)
    session_factory = session_factory_for(database)
    with sync_transaction(session_factory) as session:
        if session.scalar(select(func.count()).select_from(World)) == 0:
            import_seed_bundle(session, load_seed_bundle(), random_seed=1)
        world_id = resolve_world_id(session, world)
    result = asyncio.run(
        run_one_autonomous_turn(
            session_factory,
            world_id,
            scripted_turn_provider(1),
            minutes,
        )
    )
    with session_factory() as session:
        command_id = session.scalar(
            select(CommandExecution.id)
            .where(CommandExecution.world_id == world_id)
            .order_by(CommandExecution.started_at.desc())
            .limit(1)
        )
    console.print_json(
        data={
            "command_id": command_id,
            "turn_id": result.turn_id,
            "agent": result.agent_name,
            "provider": ScriptedProvider.provider_name,
            "model": ScriptedProvider.model_name,
        }
    )


@app.command("readiness-check")
def readiness_check(
    database: Path = typer.Option(
        Path("emergence_world.db"), help="Initialized SQLite database path."
    ),
    world: str | None = typer.Option(
        None, help="World name when database has several."
    ),
    run_tests: bool = typer.Option(
        True, "--run-tests/--skip-tests", help="Run the complete pytest suite."
    ),
) -> None:
    """Evaluate the final gate before enabling a real provider."""

    session_factory = session_factory_for(database)
    with session_factory() as session:
        try:
            world_id = resolve_world_id(session, world)
        except typer.BadParameter:
            world_id = None
        report = readiness_report(
            session,
            database=database,
            world_id=world_id,
            run_tests=run_tests,
        )
    console.print_json(data=report)


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


def parse_trace_datetime(value: str | None, option_name: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter(
            f"{option_name} must be an ISO-8601 datetime"
        ) from exc


@app.command("list-traces")
def list_traces(
    world: str | None = typer.Option(None, help="Optional world name."),
    stage: str | None = typer.Option(None, help="Require a span with this stage."),
    status: str | None = typer.Option(None, help="Command status filter."),
    started_from: str | None = typer.Option(None, "--from", help="ISO-8601 start."),
    started_to: str | None = typer.Option(None, "--to", help="ISO-8601 end."),
    offset: int = typer.Option(0, min=0),
    limit: int = typer.Option(50, min=1, max=500),
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
) -> None:
    """List bounded trace summaries without loading recorded payloads."""

    migrate_database(database)
    session_factory = session_factory_for(database)
    with session_factory() as session:
        world_id = resolve_world_id(session, world) if world is not None else None
        items = list_trace_summaries(
            session,
            world_id=world_id,
            stage=stage,
            status=status,
            started_from=parse_trace_datetime(started_from, "--from"),
            started_to=parse_trace_datetime(started_to, "--to"),
            offset=offset,
            limit=limit,
        )
    console.print_json(data={"offset": offset, "limit": limit, "items": items})


@app.command("inspect-trace")
def inspect_trace(
    command_id: str | None = typer.Option(None, "--command", help="Command identifier."),
    turn_id: str | None = typer.Option(None, "--turn", help="Turn identifier."),
    latest: bool = typer.Option(False, "--latest", help="Inspect the latest trace."),
    stage: str | None = typer.Option(None, help="Span stage filter."),
    status: str | None = typer.Option(None, help="Span status filter."),
    offset: int = typer.Option(0, min=0, help="Span offset."),
    limit: int = typer.Option(100, min=1, max=500, help="Maximum spans."),
    related_offset: int = typer.Option(
        0, min=0, help="Provider/tool/event/state-diff offset."
    ),
    related_limit: int = typer.Option(
        100, min=1, max=500, help="Maximum items per related collection."
    ),
    include_payloads: bool = typer.Option(
        False,
        "--include-payloads",
        help="Include potentially large inputs, outputs, and provider payloads.",
    ),
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
) -> None:
    """Inspect a complete structured execution trace."""

    selectors = sum((command_id is not None, turn_id is not None, latest))
    if selectors > 1:
        raise typer.BadParameter(
            "provide only one of --command, --turn, or --latest"
        )
    migrate_database(database)
    session_factory = session_factory_for(database)
    with session_factory() as session:
        if command_id is not None:
            command_record = session.get(CommandExecution, command_id)
            if command_record is None:
                raise typer.BadParameter(f"no trace found for command: {command_id}")
        elif turn_id is not None:
            command_record = session.scalar(
                select(CommandExecution)
                .join(ExecutionSpan, ExecutionSpan.command_id == CommandExecution.id)
                .where(ExecutionSpan.turn_id == turn_id)
                .order_by(CommandExecution.started_at.desc())
            )
            if command_record is None:
                raise typer.BadParameter(f"no trace found for turn: {turn_id}")
        else:
            command_record = session.scalar(
                select(CommandExecution).order_by(CommandExecution.started_at.desc())
            )
            if command_record is None:
                raise typer.BadParameter("no execution trace found")
        span_query = select(ExecutionSpan).where(
            ExecutionSpan.command_id == command_record.id
        )
        if stage is not None:
            span_query = span_query.where(ExecutionSpan.stage == stage)
        if status is not None:
            span_query = span_query.where(ExecutionSpan.status == status)
        spans = session.scalars(
            span_query.order_by(ExecutionSpan.sequence_number)
            .offset(offset)
            .limit(limit)
        ).all()
        all_command_spans = session.scalars(
            select(ExecutionSpan)
            .where(ExecutionSpan.command_id == command_record.id)
            .order_by(ExecutionSpan.sequence_number)
        ).all()
        effective_turn_id = turn_id or next(
            (span.turn_id for span in all_command_spans if span.turn_id is not None), None
        )
        diffs = session.scalars(
            select(StateDiff)
            .where(StateDiff.command_id == command_record.id)
            .order_by(StateDiff.sequence_number)
            .offset(related_offset)
            .limit(related_limit)
        ).all()
        interactions = (
            session.scalars(
                select(ProviderInteraction)
                .where(ProviderInteraction.turn_id == effective_turn_id)
                .order_by(ProviderInteraction.sequence_number)
                .offset(related_offset)
                .limit(related_limit)
            ).all()
            if effective_turn_id is not None
            else []
        )
        calls = (
            session.scalars(
                select(ToolCall)
                .where(ToolCall.turn_id == effective_turn_id)
                .order_by(ToolCall.sequence_number)
                .offset(related_offset)
                .limit(related_limit)
            ).all()
            if effective_turn_id is not None
            else []
        )
        events = (
            session.scalars(
                select(WorldEvent)
                .where(WorldEvent.turn_id == effective_turn_id)
                .order_by(WorldEvent.sequence_number)
                .offset(related_offset)
                .limit(related_limit)
            ).all()
            if effective_turn_id is not None
            else []
        )
        output = {
            "command": {
                "id": command_record.id,
                "name": command_record.command_name,
                "arguments": command_record.arguments_json,
                "status": command_record.status,
                "started_at": command_record.started_at.isoformat(),
                "completed_at": (
                    command_record.completed_at.isoformat()
                    if command_record.completed_at is not None
                    else None
                ),
                "error": command_record.error,
            },
            "turn_id": effective_turn_id,
            "span_page": {"offset": offset, "limit": limit, "count": len(spans)},
            "related_page": {
                "offset": related_offset,
                "limit": related_limit,
                "provider_interaction_count": len(interactions),
                "tool_call_count": len(calls),
                "event_count": len(events),
                "state_diff_count": len(diffs),
            },
            "spans": [
                {
                    "id": span.id,
                    "parent_span_id": span.parent_span_id,
                    "sequence_number": span.sequence_number,
                    "stage": span.stage,
                    "function_name": span.function_name,
                    "source_file": span.source_file,
                    "source_line": span.source_line,
                    **(
                        {"input": span.input_json, "output": span.output_json}
                        if include_payloads
                        else {}
                    ),
                    "status": span.status,
                    "duration_ms": span.duration_ms,
                    "error": span.error,
                }
                for span in spans
            ],
            "provider_interactions": [
                {
                    "provider": item.provider,
                    "model": item.model_name,
                    "tool_calls": item.parsed_tool_calls_json,
                    "latency_ms": item.latency_ms,
                    "cost_usd": item.cost_usd,
                    **(
                        {
                            "request": item.request_json,
                            "response": item.raw_response_json,
                        }
                        if include_payloads
                        else {}
                    ),
                }
                for item in interactions
            ],
            "tool_calls": [
                {
                    "tool_name": call.tool_name,
                    "arguments": call.arguments_json,
                    "status": call.status.value,
                    "result": call.result_json,
                    "error": call.error,
                }
                for call in calls
            ],
            "events": [
                {
                    "sequence_number": event.sequence_number,
                    "event_type": event.event_type,
                    "payload": event.payload_json,
                }
                for event in events
            ],
            "state_diffs": [
                {
                    "sequence_number": diff.sequence_number,
                    "entity_type": diff.entity_type,
                    "entity_id": diff.entity_id,
                    "path": diff.path,
                    "before": diff.before_json,
                    "after": diff.after_json,
                }
                for diff in diffs
            ],
        }
    console.print_json(data=output)


@app.command("prune-traces")
def prune_traces(
    older_than_days: int = typer.Option(30, min=0),
    keep_latest: int = typer.Option(100, min=0),
    world: str | None = typer.Option(None, help="Optional world name."),
    execute: bool = typer.Option(
        False, "--execute", help="Delete candidates; default is a dry run."
    ),
    database: Path = typer.Option(
        Path("emergence_world.db"), help="SQLite database path."
    ),
) -> None:
    """Apply the bounded trace-retention policy, dry-running by default."""

    migrate_database(database)
    session_factory = session_factory_for(database)
    with sync_transaction(session_factory) as session:
        world_id = resolve_world_id(session, world) if world is not None else None
        candidates = trace_prune_candidates(
            session,
            older_than_days=older_than_days,
            keep_latest=keep_latest,
            world_id=world_id,
        )
        deleted = delete_traces(session, candidates) if execute else 0
    console.print_json(
        data={
            "policy": {
                "older_than_days": older_than_days,
                "keep_latest": keep_latest,
                "world": world,
            },
            "dry_run": not execute,
            "candidate_count": len(candidates),
            "candidate_command_ids": candidates,
            "deleted_count": deleted,
        }
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

    migrate_database(database)
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
