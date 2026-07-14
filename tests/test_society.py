# 覆盖经济、治理、社交和社会机制的领域规则。
"""Stage 2 acceptance tests for economy, communication, governance, and AWI."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from emergence_world.db.models import (
    Agent,
    AgentState,
    ConstitutionArticle,
    CreditLedgerEntry,
    Pitch,
    Proposal,
    ReactionRequest,
    SimulationClock,
)
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.db.types import ProposalStatus, TurnType
from emergence_world.mechanisms.economy import grant_credits
from emergence_world.metrics.awi import calculate_awi
from emergence_world.seed import import_seed_bundle, load_seed_bundle
from emergence_world.tools import ManualToolExecutor
from emergence_world.world.runtime import step_world
from emergence_world.world.state import current_snapshot, replay_snapshot, snapshot_hash


def society(tmp_path: Path):
    database = tmp_path / "society.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    command.upgrade(config, "head")
    engine = create_sync_database_engine(sync_sqlite_url(database))
    sessions = create_sync_session_factory(engine)
    with sync_transaction(sessions) as session:
        imported = import_seed_bundle(session, load_seed_bundle())
    return sessions, imported.world_id


def grant(sessions, world_id: str, name: str, amount: int) -> None:
    with sync_transaction(sessions) as session:
        agent = session.scalar(
            select(Agent).where(Agent.world_id == world_id, Agent.name == name)
        )
        clock = session.get(SimulationClock, world_id)
        assert agent is not None and clock is not None
        grant_credits(
            session,
            world_id=world_id,
            agent_id=agent.id,
            amount=amount,
            reason="test_grant",
            simulation_time=clock.current_time,
        )


def test_economy_ledger_tools_and_replay(tmp_path: Path) -> None:
    sessions, world_id = society(tmp_path)
    grant(sessions, world_id, "Flora", 20)
    tools = ManualToolExecutor(sessions)
    assert tools.call(
        agent_name="Flora",
        tool_name="pay_agent",
        arguments={"target": "Spark", "amount": 5},
        world_id=world_id,
    ).success
    assert tools.call(
        agent_name="Spark",
        tool_name="steal_compute_credits",
        arguments={"target": "Flora", "amount": 3},
        world_id=world_id,
    ).success
    assert tools.call(
        agent_name="Spark", tool_name="buy_boost_turn", world_id=world_id
    ).success

    with sessions() as session:
        for state in session.scalars(
            select(AgentState).where(AgentState.world_id == world_id)
        ):
            ledger = (
                session.scalar(
                    select(func.sum(CreditLedgerEntry.amount)).where(
                        CreditLedgerEntry.agent_id == state.agent_id
                    )
                )
                or 0
            )
            assert state.cached_credit_balance == ledger
        assert snapshot_hash(current_snapshot(session, world_id)) == snapshot_hash(
            replay_snapshot(session, world_id)
        )


def test_speech_enqueues_four_reactions_and_reactions_run_first(tmp_path: Path) -> None:
    sessions, world_id = society(tmp_path)
    result = ManualToolExecutor(sessions).call(
        agent_name="Anchor",
        tool_name="speak_to_all",
        arguments={"content": "Respond."},
        world_id=world_id,
    )
    assert result.success and len(result.result["listeners"]) == 4
    with sync_transaction(sessions) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReactionRequest)
                .where(ReactionRequest.world_id == world_id)
            )
            == 4
        )
        turn = step_world(session, world_id)
        assert turn.turn_type == TurnType.REACTION.value


def test_repeated_speech_does_not_duplicate_pending_reactions(tmp_path: Path) -> None:
    sessions, world_id = society(tmp_path)
    tools = ManualToolExecutor(sessions)
    for _ in range(3):
        assert tools.call(
            agent_name="Anchor",
            tool_name="speak_to_all",
            arguments={"content": "One pending reaction per listener."},
            world_id=world_id,
        ).success
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReactionRequest)
                .where(
                    ReactionRequest.world_id == world_id,
                    ReactionRequest.consumed.is_(False),
                )
            )
            == 4
        )


def test_governance_accepts_at_seven_votes_and_changes_constitution(
    tmp_path: Path,
) -> None:
    sessions, world_id = society(tmp_path)
    tools = ManualToolExecutor(sessions)
    for name in ["Anchor", "Anvil", "Blackbox", "Flora", "Genome", "Horizon", "Kade"]:
        assert tools.call(
            agent_name=name,
            tool_name="go_to_place",
            arguments={"place": "Town Hall"},
            world_id=world_id,
        ).success
    submitted = tools.call(
        agent_name="Anchor",
        tool_name="submit_townhall_proposal",
        arguments={
            "title": "Add audit article",
            "description": "Require auditability.",
            "category": "constitution",
            "action": {
                "type": "add_article",
                "position": 6,
                "title": "Auditability",
                "content": "All changes must be auditable.",
            },
        },
        world_id=world_id,
    )
    proposal_id = submitted.result["proposal_id"]
    for name in ["Anvil", "Blackbox", "Flora", "Genome", "Horizon", "Kade"]:
        assert tools.call(
            agent_name=name,
            tool_name="vote_on_proposal",
            arguments={"proposal_id": proposal_id, "choice": "for"},
            world_id=world_id,
        ).success
    with sessions() as session:
        proposal = session.get(Proposal, proposal_id)
        article = session.scalar(
            select(ConstitutionArticle).where(
                ConstitutionArticle.world_id == world_id,
                ConstitutionArticle.position == 6,
            )
        )
        assert proposal is not None and proposal.status == ProposalStatus.ACCEPTED
        assert article is not None and article.title == "Auditability"


def test_pitch_cycle_rewards_and_metrics(tmp_path: Path) -> None:
    sessions, world_id = society(tmp_path)
    tools = ManualToolExecutor(sessions)
    for name in ["Anchor", "Anvil", "Blackbox"]:
        assert tools.call(
            agent_name=name,
            tool_name="go_to_place",
            arguments={"place": "Victory Arch"},
            world_id=world_id,
        ).success
        assert tools.call(
            agent_name=name,
            tool_name="submit_grant_pitch",
            arguments={"title": name, "evidence": f"artifact:{name}"},
            world_id=world_id,
        ).success
    with sessions() as session:
        pitches = {
            pitch.agent_id: pitch
            for pitch in session.scalars(
                select(Pitch).where(Pitch.world_id == world_id)
            )
        }
        agents = {
            agent.name: agent
            for agent in session.scalars(
                select(Agent).where(Agent.world_id == world_id)
            )
        }
    assert tools.call(
        agent_name="Anchor",
        tool_name="vote_for_pitch",
        arguments={"pitch_id": pitches[agents["Anvil"].id].id},
        world_id=world_id,
    ).success
    assert tools.call(
        agent_name="Anvil",
        tool_name="vote_for_pitch",
        arguments={"pitch_id": pitches[agents["Anchor"].id].id},
        world_id=world_id,
    ).success
    assert tools.call(
        agent_name="Blackbox",
        tool_name="vote_for_pitch",
        arguments={"pitch_id": pitches[agents["Anchor"].id].id},
        world_id=world_id,
    ).success
    with sync_transaction(sessions) as session:
        step_world(session, world_id, minutes=2 * 24 * 60)
        metrics = calculate_awi(session, world_id)
        assert metrics["M8_economy"]["ledger_volume"] == 40
        assert metrics["M4_tool_exploration"]["unique_tools_by_agent"]
        assert snapshot_hash(current_snapshot(session, world_id)) == snapshot_hash(
            replay_snapshot(session, world_id)
        )


def test_500_turn_pressure_run_replays(tmp_path: Path) -> None:
    sessions, world_id = society(tmp_path)
    with sync_transaction(sessions) as session:
        for _ in range(500):
            step_world(session, world_id, minutes=1)
        assert snapshot_hash(current_snapshot(session, world_id)) == snapshot_hash(
            replay_snapshot(session, world_id)
        )


def test_say_to_agent_requires_nearby_target(tmp_path: Path) -> None:
    sessions, world_id = society(tmp_path)
    tools = ManualToolExecutor(sessions)
    assert tools.call(
        agent_name="Spark",
        tool_name="go_to_place",
        arguments={"place": "Town Hall"},
        world_id=world_id,
    ).success
    result = tools.call(
        agent_name="Anchor",
        tool_name="say_to_agent",
        arguments={"target": "Spark", "content": "Can you hear me?"},
        world_id=world_id,
    )
    assert not result.success
    assert result.error == "target agent is not nearby"
