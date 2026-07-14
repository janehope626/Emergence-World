# 覆盖实验清单生成、稳定哈希、代码快照与就绪性检查。
"""Experiment run manifest audit acceptance tests."""

from __future__ import annotations

from pathlib import Path
import subprocess

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from typer.testing import CliRunner

from emergence_world.cli import app
from emergence_world.db.models import ExperimentRun
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.db.types import ExperimentRunStatus
from emergence_world.experiments.manifest import build_run_manifest, git_snapshot
from emergence_world.experiments.readiness import readiness_report
from emergence_world.seed import import_seed_bundle, load_seed_bundle


def initialized_database(tmp_path: Path):
    database = tmp_path / "manifest.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    command.upgrade(config, "head")
    engine = create_sync_database_engine(sync_sqlite_url(database))
    session_factory = create_sync_session_factory(engine)
    with sync_transaction(session_factory) as session:
        imported = import_seed_bundle(session, load_seed_bundle(), random_seed=42)
    return database, session_factory, imported.world_id


def test_create_run_persists_manifest_and_inspect_displays_core_fields(
    tmp_path: Path,
) -> None:
    database, session_factory, _ = initialized_database(tmp_path)
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "create-run",
            "--run-id",
            "manifest-test",
            "--provider",
            "scripted",
            "--turns",
            "20",
            "--random-seed",
            "42",
            "--database",
            str(database),
        ],
    )
    inspected = runner.invoke(
        app, ["inspect-run", "manifest-test", "--database", str(database)]
    )

    assert created.exit_code == inspected.exit_code == 0
    assert '"seed_hash":' in inspected.stdout
    assert '"provider_name": "scripted"' in inspected.stdout
    assert '"random_seed": 42' in inspected.stdout
    with session_factory() as session:
        run = session.scalar(
            select(ExperimentRun).where(ExperimentRun.run_id == "manifest-test")
        )
        assert run is not None
        assert run.status == ExperimentRunStatus.CREATED
        assert run.seed_hash and run.config_hash and run.tool_registry_hash
        assert run.prompt_template_version == "agent_turn_v1"
        assert run.prompt_hash
        assert run.provider_parameters_json["smoke_config"]["max_retries"] == 2
        assert isinstance(run.environment_json["git_dirty"], bool)
        assert isinstance(run.environment_json["dirty_files"], list)
        assert run.environment_json["git_branch"] == "main"
        assert run.environment_json["git_commit"]


def test_manifest_hashes_are_stable_for_same_seed_config_and_tools(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_database, first_factory, first_world_id = initialized_database(first_root)
    second_database, second_factory, second_world_id = initialized_database(second_root)

    with first_factory() as session:
        first = build_run_manifest(
            session,
            world_id=first_world_id,
            provider_name="scripted",
            provider_model="deterministic-sequence",
            provider_parameters={},
            random_seed=42,
            simulation_minutes_per_turn=30,
            max_turns=20,
            database_path=first_database,
        )
    with second_factory() as session:
        second = build_run_manifest(
            session,
            world_id=second_world_id,
            provider_name="scripted",
            provider_model="deterministic-sequence",
            provider_parameters={},
            random_seed=42,
            simulation_minutes_per_turn=30,
            max_turns=20,
            database_path=second_database,
        )

    assert first.seed_hash == second.seed_hash
    assert first.config_hash == second.config_hash
    assert first.tool_registry_hash == second.tool_registry_hash
    assert first.initial_state_hash == second.initial_state_hash
    assert first.prompt_hash == second.prompt_hash


def test_duplicate_run_id_is_rejected(tmp_path: Path) -> None:
    database, _, _ = initialized_database(tmp_path)
    runner = CliRunner()
    arguments = [
        "create-run",
        "--run-id",
        "duplicate",
        "--provider",
        "scripted",
        "--turns",
        "1",
        "--random-seed",
        "42",
        "--database",
        str(database),
    ]

    assert runner.invoke(app, arguments).exit_code == 0
    duplicate = runner.invoke(app, arguments)

    assert duplicate.exit_code != 0
    assert "run_id already exists: duplicate" in duplicate.output


def test_openai_run_manifest_is_created_without_api_key_or_request(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "openai-secret-must-not-persist"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    database, session_factory, _ = initialized_database(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "create-run",
            "--run-id",
            "openai-manifest",
            "--provider",
            "openai",
            "--model",
            "mock-model",
            "--turns",
            "1",
            "--input-cost-per-million-tokens-usd",
            "1",
            "--output-cost-per-million-tokens-usd",
            "2",
            "--database",
            str(database),
        ],
    )

    assert result.exit_code == 0
    with session_factory() as session:
        run = session.scalar(
            select(ExperimentRun).where(ExperimentRun.run_id == "openai-manifest")
        )
        assert run is not None
        assert run.provider_name == "openai"
        assert run.provider_model == "mock-model"
        assert run.provider_parameters_json["api"] == "responses"
        assert run.provider_parameters_json["smoke_config"]["max_turns"] == 1
        assert run.provider_parameters_json["smoke_config"]["max_tool_calls_per_turn"] == 1
        assert secret not in str(run.provider_parameters_json)


def test_openai_run_requires_explicit_external_provider_permission(tmp_path: Path) -> None:
    database, _, _ = initialized_database(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "run-autonomous",
            "--provider",
            "openai",
            "--model",
            "mock-model",
            "--turns",
            "1",
            "--input-cost-per-million-tokens-usd",
            "1",
            "--output-cost-per-million-tokens-usd",
            "2",
            "--database",
            str(database),
        ],
    )

    assert result.exit_code != 0
    assert "requires explicit --allow-external-provider" in result.output


def test_run_autonomous_creates_and_completes_manifest(tmp_path: Path) -> None:
    database, session_factory, _ = initialized_database(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-autonomous",
            "--run-id",
            "autonomous-manifest",
            "--turns",
            "2",
            "--provider",
            "scripted",
            "--database",
            str(database),
        ],
    )

    assert result.exit_code == 0
    assert '"run_id": "autonomous-manifest"' in result.stdout
    with session_factory() as session:
        run = session.scalar(
            select(ExperimentRun).where(
                ExperimentRun.run_id == "autonomous-manifest"
            )
        )
        assert run is not None
        assert run.status == ExperimentRunStatus.COMPLETED
        assert run.completed_at is not None
        assert run.max_turns == 2


def test_git_snapshot_records_clean_and_dirty_worktrees(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repository, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    tracked = repository / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repository, check=True)

    clean = git_snapshot(repository)
    tracked.write_text("dirty\n", encoding="utf-8")
    dirty = git_snapshot(repository)

    assert clean["git_dirty"] is False
    assert clean["dirty_files"] == []
    assert clean["git_branch"] == "main"
    assert clean["git_commit"]
    assert dirty["git_dirty"] is True
    assert dirty["dirty_files"] == ["tracked.txt"]


def test_formal_run_rejects_dirty_git_and_readiness_reports_gate(
    tmp_path: Path, monkeypatch
) -> None:
    database, _, _ = initialized_database(tmp_path)
    runner = CliRunner()
    dirty_snapshot = {
        "git_dirty": True,
        "dirty_files": ["test-dirty-file"],
        "git_branch": "main",
        "git_commit": "test-commit",
    }
    monkeypatch.setattr(
        "emergence_world.experiments.manifest.git_snapshot",
        lambda root: dirty_snapshot,
    )
    monkeypatch.setattr(
        "emergence_world.experiments.readiness.git_snapshot",
        lambda root: dirty_snapshot,
    )

    formal = runner.invoke(
        app,
        [
            "create-run",
            "--run-id",
            "formal-dirty",
            "--provider",
            "scripted",
            "--turns",
            "1",
            "--mode",
            "formal",
            "--database",
            str(database),
        ],
    )
    assert formal.exit_code != 0
    assert "formal experiment mode requires a clean git worktree" in formal.output

    assert (
        runner.invoke(
            app,
            [
                "create-run",
                "--run-id",
                "readiness-development",
                "--provider",
                "scripted",
                "--turns",
                "1",
                "--database",
                str(database),
            ],
        ).exit_code
        == 0
    )
    readiness = runner.invoke(
        app, ["readiness-check", "--skip-tests", "--database", str(database)]
    )

    assert readiness.exit_code == 0
    assert '"tests_status": "not_run"' in readiness.stdout
    assert '"migration_status": "passed"' in readiness.stdout
    assert '"replay_status": "passed"' in readiness.stdout
    assert '"manifest_complete": true' in readiness.stdout
    assert '"audit_tables_present": true' in readiness.stdout
    assert '"ready_for_real_provider": false' in readiness.stdout
    assert '"prompt_hash_present": true' in readiness.stdout
    assert '"provider_budget_config_present": true' in readiness.stdout
    assert '"secret_leak_check_passed": true' in readiness.stdout


def test_missing_prompt_or_budget_blocks_readiness(tmp_path: Path) -> None:
    database, session_factory, world_id = initialized_database(tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(
            app,
            [
                "create-run",
                "--run-id",
                "incomplete-readiness",
                "--provider",
                "scripted",
                "--turns",
                "1",
                "--database",
                str(database),
            ],
        ).exit_code
        == 0
    )

    with sync_transaction(session_factory) as session:
        run = session.scalar(
            select(ExperimentRun).where(ExperimentRun.run_id == "incomplete-readiness")
        )
        assert run is not None
        run.prompt_hash = None
        run.provider_parameters_json = {}
    with session_factory() as session:
        report = readiness_report(
            session, database=database, world_id=world_id, run_tests=False
        )

    assert report["prompt_hash_present"] is False
    assert report["provider_budget_config_present"] is False
    assert report["ready_for_real_provider"] is False


def test_manifest_redacts_provider_secrets(tmp_path: Path, monkeypatch) -> None:
    secret = "manifest-secret-must-not-persist"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    database, session_factory, world_id = initialized_database(tmp_path)

    with session_factory() as session:
        manifest = build_run_manifest(
            session,
            world_id=world_id,
            provider_name="recording",
            provider_model="offline-recording",
            provider_parameters={"api_key": secret, "nested": {"value": secret}},
            random_seed=42,
            simulation_minutes_per_turn=30,
            max_turns=1,
            database_path=database,
        )

    assert secret not in str(manifest.provider_parameters_json)
    assert manifest.provider_parameters_json["api_key"] == "[REDACTED]"
