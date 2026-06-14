"""Validated, transactional, and auditable manual tool execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from emergence_world.agents.models import ToolExecutionResult
from emergence_world.db.models import (
    Agent,
    AgentState,
    Landmark,
    SimulationClock,
    ToolCall,
    Turn,
    WorldEvent,
)
from emergence_world.db.types import ToolCallStatus, TurnStatus, TurnType
from emergence_world.tools.registry import ToolRegistry


class ToolValidationError(ValueError):
    pass


class ManualToolExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        registry: ToolRegistry | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry or ToolRegistry()

    def call(
        self,
        *,
        agent_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        world_id: str | None = None,
    ) -> ToolExecutionResult:
        arguments = dict(arguments or {})
        with self._session_factory() as session, session.begin():
            agent, state, landmark = self._resolve_agent(
                session, agent_name=agent_name, world_id=world_id
            )
            turn = self._create_turn(session, agent)
            tool_call = ToolCall(
                world_id=agent.world_id,
                turn_id=turn.id,
                agent_id=agent.id,
                sequence_number=1,
                tool_name=tool_name,
                tool_version="unknown",
                arguments_json=arguments,
                status=ToolCallStatus.REQUESTED,
            )
            session.add(tool_call)
            session.flush()

            registered = self._registry.get(session, tool_name)
            try:
                self._validate(registered, state, landmark, arguments)
            except ToolValidationError as exc:
                return self._fail(
                    turn,
                    tool_call,
                    ToolCallStatus.VALIDATION_FAILED,
                    str(exc),
                    self._simulation_time(session, agent.world_id),
                )

            assert registered is not None
            tool_call.tool_definition_id = registered.definition.id
            tool_call.tool_version = registered.definition.version
            try:
                with session.begin_nested():
                    handler_arguments = {
                        **arguments,
                        "_agent_id": agent.id,
                        "_tool_call_id": tool_call.id,
                    }
                    assert registered.handler is not None
                    output = registered.handler(
                        session, agent.world_id, handler_arguments
                    )
                    self._validate_handler_output(
                        session,
                        output.events,
                        registered.definition.produced_event_types,
                    )
                    for pending in output.events:
                        session.add(
                            WorldEvent(
                                world_id=agent.world_id,
                                turn_id=turn.id,
                                tool_call_id=tool_call.id,
                                sequence_number=self._next_event_sequence(
                                    session, agent.world_id
                                ),
                                event_type=pending.event_type,
                                payload_json=pending.payload,
                                simulation_time=self._simulation_time(
                                    session, agent.world_id
                                ),
                            )
                        )
                        session.flush()
            except Exception as exc:
                return self._fail(
                    turn,
                    tool_call,
                    ToolCallStatus.EXECUTION_FAILED,
                    str(exc),
                    self._simulation_time(session, agent.world_id),
                )

            tool_call.status = ToolCallStatus.SUCCEEDED
            tool_call.result_json = output.result
            tool_call.completed_at = self._simulation_time(session, agent.world_id)
            turn.status = TurnStatus.COMPLETED
            turn.tool_calls_used = 1
            turn.ended_at = tool_call.completed_at
            return ToolExecutionResult(
                call_id=tool_call.id,
                tool_name=tool_name,
                success=True,
                result=output.result,
            )

    @staticmethod
    def _resolve_agent(
        session: Session, *, agent_name: str, world_id: str | None
    ) -> tuple[Agent, AgentState, Landmark | None]:
        query = (
            select(Agent, AgentState, Landmark)
            .join(AgentState, AgentState.agent_id == Agent.id)
            .outerjoin(Landmark, Landmark.id == AgentState.current_landmark_id)
            .where(Agent.name == agent_name)
        )
        if world_id is not None:
            query = query.where(Agent.world_id == world_id)
        rows = session.execute(query).all()
        if not rows:
            raise ToolValidationError(f"unknown agent: {agent_name}")
        if len(rows) > 1:
            raise ToolValidationError("agent name is ambiguous; provide world_id")
        agent, state, landmark = rows[0]
        return agent, state, landmark

    @staticmethod
    def _validate(
        registered: Any,
        state: AgentState,
        landmark: Landmark | None,
        arguments: dict[str, Any],
    ) -> None:
        if not state.is_alive:
            raise ToolValidationError("dead agents cannot call tools")
        if registered is None:
            raise ToolValidationError("tool does not exist")
        locations = registered.definition.availability_rules.get("locations", [])
        if locations and (landmark is None or landmark.name not in locations):
            raise ToolValidationError(
                f"tool is unavailable at {landmark.name if landmark else 'no location'}"
            )
        if registered.handler is None:
            raise ToolValidationError("tool handler is not implemented")
        try:
            validate(instance=arguments, schema=registered.definition.argument_schema)
        except JsonSchemaValidationError as exc:
            raise ToolValidationError(f"invalid arguments: {exc.message}") from exc

    @staticmethod
    def _create_turn(session: Session, agent: Agent) -> Turn:
        sequence = (
            session.scalar(
                select(func.max(Turn.sequence_number)).where(
                    Turn.world_id == agent.world_id
                )
            )
            or 0
        ) + 1
        turn = Turn(
            world_id=agent.world_id,
            agent_id=agent.id,
            sequence_number=sequence,
            turn_type=TurnType.REGULAR,
            status=TurnStatus.RUNNING,
            tool_call_budget=1,
        )
        session.add(turn)
        session.flush()
        return turn

    @staticmethod
    def _validate_handler_output(
        session: Session, events: tuple[Any, ...], allowed_event_types: list[str]
    ) -> None:
        audit_models = (Turn, ToolCall, WorldEvent)
        changed = [
            item
            for item in set(session.new) | set(session.dirty) | set(session.deleted)
            if not isinstance(item, audit_models)
        ]
        if changed and not events:
            raise RuntimeError("state-changing handler produced no world event")
        undeclared = {event.event_type for event in events} - set(allowed_event_types)
        if undeclared:
            raise RuntimeError(f"handler produced undeclared event types: {undeclared}")

    @staticmethod
    def _next_event_sequence(session: Session, world_id: str) -> int:
        return (
            session.scalar(
                select(func.max(WorldEvent.sequence_number)).where(
                    WorldEvent.world_id == world_id
                )
            )
            or 0
        ) + 1

    @staticmethod
    def _simulation_time(session: Session, world_id: str) -> datetime:
        clock = session.get(SimulationClock, world_id)
        if clock is None:
            raise RuntimeError("simulation clock not found")
        return clock.current_time

    @staticmethod
    def _fail(
        turn: Turn,
        tool_call: ToolCall,
        status: ToolCallStatus,
        error: str,
        completed_at: datetime,
    ) -> ToolExecutionResult:
        tool_call.status = status
        tool_call.error = error
        tool_call.completed_at = completed_at
        turn.status = TurnStatus.FAILED
        turn.tool_calls_used = 1
        turn.ended_at = completed_at
        return ToolExecutionResult(
            call_id=tool_call.id,
            tool_name=tool_call.tool_name,
            success=False,
            error=error,
        )
