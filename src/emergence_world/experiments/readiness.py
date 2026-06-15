"""Readiness checks for audited autonomous experiment execution."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    AgentState,
    CreditLedgerEntry,
    ExperimentRun,
    ProviderInteraction,
    ToolCall,
    Turn,
    WorldEvent,
)
from emergence_world.db.session import sync_sqlite_url
from emergence_world.db.types import ToolCallStatus
from emergence_world.experiments.manifest import git_snapshot
from emergence_world.agents.providers.security import contains_configured_secret
from emergence_world.agents.providers.smoke import ProviderSmokeConfig
from emergence_world.world.state import current_snapshot, replay_snapshot, snapshot_hash

AUDIT_TABLES = {
    "experiment_runs",
    "turns",
    "tool_calls",
    "world_events",
    "provider_interactions",
    "context_builds",
    "context_memory_candidates",
    "context_memory_selections",
}


def readiness_report(
    session: Session,
    *,
    database: Path,
    world_id: str | None,
    run_tests: bool = True,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    root = repository_root or Path.cwd()
    git = git_snapshot(root)
    tests_status = _tests_status(root) if run_tests else "not_run"
    migration_status = _migration_status(database)
    replay_status, replay_details = _replay_status(session, world_id)
    if session.bind is None:
        raise ValueError("database session is not bound")
    tables = set(inspect(session.bind).get_table_names())
    audit_tables_present = AUDIT_TABLES <= tables
    manifest = session.scalar(
        select(ExperimentRun).order_by(ExperimentRun.started_at.desc())
    )
    manifest_complete = _manifest_complete(manifest)
    prompt_hash_present = bool(
        manifest and manifest.prompt_template_version and manifest.prompt_hash
    )
    provider_budget_config_present = _provider_budget_config_present(manifest)
    secret_leak_check_passed = _secret_leak_check(session)
    audit_diff_report = _audit_diff_report(session, world_id)
    ready = all(
        (
            tests_status == "passed",
            migration_status == "passed",
            replay_status == "passed",
            git["git_dirty"] is False,
            manifest_complete,
            prompt_hash_present,
            provider_budget_config_present,
            secret_leak_check_passed,
            audit_tables_present,
            not any(audit_diff_report.values()),
        )
    )
    return {
        "tests_status": tests_status,
        "migration_status": migration_status,
        "replay_status": replay_status,
        "git_dirty": git["git_dirty"],
        "dirty_files": git["dirty_files"],
        "git_branch": git["git_branch"],
        "git_commit": git["git_commit"],
        "manifest_complete": manifest_complete,
        "prompt_hash_present": prompt_hash_present,
        "provider_budget_config_present": provider_budget_config_present,
        "secret_leak_check_passed": secret_leak_check_passed,
        "audit_tables_present": audit_tables_present,
        "ready_for_real_provider": ready,
        "replay_details": replay_details,
        "audit_diff_report": audit_diff_report,
    }


def _tests_status(root: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return "passed" if result.returncode == 0 else "failed"


def _migration_status(database: Path) -> str:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    try:
        command.check(config)
    except Exception:
        return "failed"
    return "passed"


def _replay_status(
    session: Session, world_id: str | None
) -> tuple[str, dict[str, Any]]:
    if world_id is None:
        return "unavailable", {}
    current = snapshot_hash(current_snapshot(session, world_id))
    replayed = snapshot_hash(replay_snapshot(session, world_id))
    return (
        "passed" if current == replayed else "failed",
        {"current_hash": current, "replayed_hash": replayed, "matches": current == replayed},
    )


def _manifest_complete(run: ExperimentRun | None) -> bool:
    if run is None:
        return False
    environment = run.environment_json
    return all(
        (
            run.git_commit,
            run.seed_version,
            run.seed_hash,
            run.config_hash,
            run.initial_state_hash,
            run.context_builder_version,
            run.retrieval_policy_version,
            run.prompt_template_version,
            run.prompt_hash,
            run.tool_registry_hash,
            run.provider_name,
            run.provider_model,
            run.database_path,
            environment.get("git_dirty") is not None,
            isinstance(environment.get("dirty_files"), list),
            environment.get("git_branch"),
            environment.get("git_commit"),
        )
    )


def _provider_budget_config_present(run: ExperimentRun | None) -> bool:
    if run is None:
        return False
    value = run.provider_parameters_json.get("smoke_config")
    if not isinstance(value, dict):
        return False
    try:
        ProviderSmokeConfig.model_validate(value)
    except ValueError:
        return False
    return True


def _secret_leak_check(session: Session) -> bool:
    manifests = session.scalars(select(ExperimentRun)).all()
    interactions = session.scalars(select(ProviderInteraction)).all()
    return not any(
        contains_configured_secret(value)
        for value in [
            *[
                {
                    "provider_parameters": run.provider_parameters_json,
                    "environment": run.environment_json,
                }
                for run in manifests
            ],
            *[
                {
                    "request": interaction.request_json,
                    "response": interaction.raw_response_json,
                    "parse_error": interaction.parse_error,
                }
                for interaction in interactions
            ],
        ]
    )


def _audit_diff_report(session: Session, world_id: str | None) -> dict[str, int]:
    if world_id is None:
        return {
            "foreign_key_violations": 0,
            "invalid_event_attribution": 0,
            "agent_events_without_successful_call": 0,
            "turn_call_count_mismatch": 0,
            "credit_balance_mismatch": 0,
        }
    foreign_key_violations = len(
        session.connection().exec_driver_sql("PRAGMA foreign_key_check").all()
    )
    invalid_event_attribution = (
        session.scalar(
            select(func.count())
            .select_from(WorldEvent)
            .where(
                WorldEvent.world_id == world_id,
                (
                    (WorldEvent.tool_call_id.is_(None) & WorldEvent.system_rule.is_(None))
                    | (
                        WorldEvent.tool_call_id.is_not(None)
                        & WorldEvent.system_rule.is_not(None)
                    )
                ),
            )
        )
        or 0
    )
    unsuccessful_events = (
        session.scalar(
            select(func.count())
            .select_from(WorldEvent)
            .join(ToolCall, ToolCall.id == WorldEvent.tool_call_id)
            .where(
                WorldEvent.world_id == world_id,
                ToolCall.status != ToolCallStatus.SUCCEEDED,
            )
        )
        or 0
    )
    turns = session.scalars(select(Turn).where(Turn.world_id == world_id)).all()
    turn_mismatch = sum(
        turn.tool_calls_used
        != (
            session.scalar(
                select(func.count())
                .select_from(ToolCall)
                .where(ToolCall.turn_id == turn.id)
            )
            or 0
        )
        for turn in turns
    )
    states = session.scalars(
        select(AgentState).where(AgentState.world_id == world_id)
    ).all()
    credit_mismatch = sum(
        state.cached_credit_balance
        != (
            session.scalar(
                select(func.sum(CreditLedgerEntry.amount)).where(
                    CreditLedgerEntry.world_id == world_id,
                    CreditLedgerEntry.agent_id == state.agent_id,
                )
            )
            or 0
        )
        for state in states
    )
    return {
        "foreign_key_violations": foreign_key_violations,
        "invalid_event_attribution": invalid_event_attribution,
        "agent_events_without_successful_call": unsuccessful_events,
        "turn_call_count_mismatch": turn_mismatch,
        "credit_balance_mismatch": credit_mismatch,
    }
