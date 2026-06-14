"""Turn-bound tool execution for autonomous agent loops."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.agents.models import RequestedToolCall, ToolExecutionResult
from emergence_world.db.models import Agent, AgentState, Landmark, ToolCall, Turn, WorldEvent
from emergence_world.db.types import ToolCallStatus
from emergence_world.tools.executor import ManualToolExecutor, ToolValidationError
from emergence_world.tools.registry import ToolRegistry


class AutonomousToolExecutor:
    def __init__(
        self, session: Session, turn: Turn, registry: ToolRegistry | None = None
    ) -> None:
        self._session = session
        self._turn = turn
        self._registry = registry or ToolRegistry()

    async def execute(
        self, *, agent_id: str, tool_call: RequestedToolCall
    ) -> ToolExecutionResult:
        if agent_id != self._turn.agent_id:
            raise ValueError("tool executor agent does not match turn agent")
        agent, state, landmark = self._resolve_agent(agent_id)
        sequence = (
            self._session.scalar(
                select(func.max(ToolCall.sequence_number)).where(
                    ToolCall.turn_id == self._turn.id
                )
            )
            or 0
        ) + 1
        record = ToolCall(
            world_id=self._turn.world_id,
            turn_id=self._turn.id,
            agent_id=agent.id,
            sequence_number=sequence,
            tool_name=tool_call.tool_name,
            tool_version="unknown",
            arguments_json=tool_call.arguments,
            status=ToolCallStatus.REQUESTED,
        )
        self._session.add(record)
        self._session.flush()
        registered = self._registry.get(self._session, tool_call.tool_name)
        try:
            ManualToolExecutor._validate(registered, state, landmark, tool_call.arguments)
        except ToolValidationError as exc:
            return self._fail(record, tool_call, ToolCallStatus.VALIDATION_FAILED, str(exc))

        assert registered is not None and registered.handler is not None
        record.tool_definition_id = registered.definition.id
        record.tool_version = registered.definition.version
        try:
            with self._session.begin_nested():
                output = registered.handler(
                    self._session,
                    self._turn.world_id,
                    {
                        **tool_call.arguments,
                        "_agent_id": agent.id,
                        "_tool_call_id": record.id,
                    },
                )
                ManualToolExecutor._validate_handler_output(
                    self._session,
                    output.events,
                    registered.definition.produced_event_types,
                )
                for pending in output.events:
                    self._session.add(
                        WorldEvent(
                            world_id=self._turn.world_id,
                            turn_id=self._turn.id,
                            tool_call_id=record.id,
                            sequence_number=ManualToolExecutor._next_event_sequence(
                                self._session, self._turn.world_id
                            ),
                            event_type=pending.event_type,
                            payload_json=pending.payload,
                            simulation_time=ManualToolExecutor._simulation_time(
                                self._session, self._turn.world_id
                            ),
                        )
                    )
                    self._session.flush()
        except Exception as exc:
            return self._fail(
                record, tool_call, ToolCallStatus.EXECUTION_FAILED, str(exc)
            )

        record.status = ToolCallStatus.SUCCEEDED
        record.result_json = output.result
        record.completed_at = ManualToolExecutor._simulation_time(
            self._session, self._turn.world_id
        )
        self._turn.tool_calls_used = sequence
        return ToolExecutionResult(
            call_id=tool_call.call_id,
            tool_name=tool_call.tool_name,
            success=True,
            result=output.result,
        )

    def _resolve_agent(self, agent_id: str) -> tuple[Agent, AgentState, Landmark | None]:
        row = self._session.execute(
            select(Agent, AgentState, Landmark)
            .join(AgentState, AgentState.agent_id == Agent.id)
            .outerjoin(Landmark, Landmark.id == AgentState.current_landmark_id)
            .where(Agent.id == agent_id, Agent.world_id == self._turn.world_id)
        ).one_or_none()
        if row is None:
            raise ToolValidationError("agent not found in world")
        agent, state, landmark = row
        return agent, state, landmark

    def _fail(
        self,
        record: ToolCall,
        requested: RequestedToolCall,
        status: ToolCallStatus,
        error: str,
    ) -> ToolExecutionResult:
        record.status = status
        record.error = error
        record.completed_at = ManualToolExecutor._simulation_time(
            self._session, self._turn.world_id
        )
        self._turn.tool_calls_used = record.sequence_number
        return ToolExecutionResult(
            call_id=requested.call_id,
            tool_name=requested.tool_name,
            success=False,
            error=error,
        )
