"""Stable experiment run manifest construction and persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from json import dumps
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.agents.assembly import AUTONOMOUS_CONTEXT_VERSION
from emergence_world.agents.memory_context import CONTEXT_POLICY_VERSION
from emergence_world.agents.prompts import AGENT_TURN_PROMPT_VERSION, agent_turn_prompt_hash
from emergence_world.agents.providers.security import redact_secrets
from emergence_world.db.models import (
    Experiment,
    ExperimentRun,
    SeedDocument,
    ToolDefinition,
    World,
)
from emergence_world.db.types import ExperimentRunStatus
from emergence_world.world.state import current_snapshot, snapshot_hash


@dataclass(frozen=True, slots=True)
class RunManifest:
    git_commit: str | None
    seed_version: str | None
    seed_hash: str
    config_hash: str
    random_seed: int
    initial_state_hash: str
    context_builder_version: str
    retrieval_policy_version: str
    prompt_template_version: str | None
    prompt_hash: str | None
    tool_registry_hash: str
    provider_name: str
    provider_model: str | None
    provider_parameters_json: dict[str, Any]
    simulation_minutes_per_turn: int
    max_turns: int
    database_path: str
    dependency_lock_hash: str | None
    environment_json: dict[str, Any]


def stable_hash(value: Any) -> str:
    canonical = dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_run_manifest(
    session: Session,
    *,
    world_id: str,
    provider_name: str,
    provider_model: str | None,
    provider_parameters: dict[str, Any],
    random_seed: int,
    simulation_minutes_per_turn: int,
    max_turns: int,
    database_path: Path,
    experiment_mode: str = "development",
    repository_root: Path | None = None,
) -> RunManifest:
    world = session.get(World, world_id)
    if world is None:
        raise ValueError("world not found")
    experiment = session.get(Experiment, world.experiment_id)
    if experiment is None:
        raise ValueError("experiment not found")
    documents = session.scalars(
        select(SeedDocument)
        .where(SeedDocument.world_id == world_id)
        .order_by(SeedDocument.document_type, SeedDocument.version, SeedDocument.source_path)
    ).all()
    tools = session.scalars(
        select(ToolDefinition).order_by(ToolDefinition.name, ToolDefinition.version)
    ).all()
    root = repository_root or Path.cwd()
    git = git_snapshot(root)
    if experiment_mode == "formal" and git["git_dirty"]:
        raise ValueError("formal experiment mode requires a clean git worktree")
    return RunManifest(
        git_commit=git["git_commit"],
        seed_version=str(
            world.config_json.get("seed_version") or experiment.config_version
        ),
        seed_hash=stable_hash(
            [
                {
                    "document_type": item.document_type,
                    "title": item.title,
                    "version": item.version,
                    "source_path": item.source_path,
                    "source_sha256": item.source_sha256,
                }
                for item in documents
            ]
        ),
        config_hash=stable_hash(
            {
                "experiment_config_version": experiment.config_version,
                "experiment_config": experiment.config_json,
                "world_config": world.config_json,
            }
        ),
        random_seed=random_seed,
        initial_state_hash=snapshot_hash(current_snapshot(session, world_id)),
        context_builder_version=AUTONOMOUS_CONTEXT_VERSION,
        retrieval_policy_version=CONTEXT_POLICY_VERSION,
        prompt_template_version=AGENT_TURN_PROMPT_VERSION,
        prompt_hash=agent_turn_prompt_hash(root / "prompts" / "agent_turn_v1.md"),
        tool_registry_hash=stable_hash(
            [
                {
                    "name": item.name,
                    "version": item.version,
                    "description": item.description,
                    "argument_schema": item.argument_schema,
                    "result_schema": item.result_schema,
                    "availability_rules": item.availability_rules,
                    "produced_event_types": item.produced_event_types,
                    "is_active": item.is_active,
                }
                for item in tools
            ]
        ),
        provider_name=provider_name,
        provider_model=provider_model,
        provider_parameters_json=redact_secrets(provider_parameters),
        simulation_minutes_per_turn=simulation_minutes_per_turn,
        max_turns=max_turns,
        database_path=str(database_path.resolve()),
        dependency_lock_hash=_dependency_lock_hash(root),
        environment_json={
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": sys.executable,
            "experiment_mode": experiment_mode,
            **git,
        },
    )


def create_experiment_run(
    session: Session,
    *,
    run_id: str,
    world_id: str,
    provider_name: str,
    provider_model: str | None,
    provider_parameters: dict[str, Any],
    random_seed: int,
    simulation_minutes_per_turn: int,
    max_turns: int,
    database_path: Path,
    status: ExperimentRunStatus = ExperimentRunStatus.CREATED,
    experiment_mode: str = "development",
) -> ExperimentRun:
    if session.scalar(select(ExperimentRun.id).where(ExperimentRun.run_id == run_id)):
        raise ValueError(f"run_id already exists: {run_id}")
    world = session.get(World, world_id)
    if world is None:
        raise ValueError("world not found")
    manifest = build_run_manifest(
        session,
        world_id=world_id,
        provider_name=provider_name,
        provider_model=provider_model,
        provider_parameters=provider_parameters,
        random_seed=random_seed,
        simulation_minutes_per_turn=simulation_minutes_per_turn,
        max_turns=max_turns,
        database_path=database_path,
        experiment_mode=experiment_mode,
    )
    run = ExperimentRun(
        run_id=run_id,
        experiment_id=world.experiment_id,
        world_id=world_id,
        status=status,
        **asdict(manifest),
    )
    session.add(run)
    session.flush()
    return run


def git_snapshot(root: Path | None = None) -> dict[str, Any]:
    repository = root or Path.cwd()
    commit = _git_output(repository, ["rev-parse", "HEAD"])
    branch = _git_output(repository, ["branch", "--show-current"])
    status = _git_output(repository, ["status", "--porcelain", "--untracked-files=all"])
    dirty_files = _dirty_files(status or "")
    return {
        "git_dirty": bool(dirty_files) if status is not None else None,
        "dirty_files": dirty_files,
        "git_branch": branch or None,
        "git_commit": commit or None,
    }


def _git_output(root: Path, arguments: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.rstrip("\r\n")


def _dirty_files(status: str) -> list[str]:
    paths: list[str] = []
    for line in status.splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", maxsplit=1)[1]
        paths.append(path)
    return sorted(paths)


def _file_hash(path: Path) -> str | None:
    try:
        content = path.read_bytes()
    except OSError:
        return None
    return sha256(content).hexdigest()


def _dependency_lock_hash(root: Path) -> str | None:
    for name in ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.lock"):
        if digest := _file_hash(root / name):
            return digest
    return None
