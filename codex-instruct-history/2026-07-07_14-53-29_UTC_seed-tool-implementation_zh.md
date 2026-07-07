# 2026-07-07 本轮对话记录：Seed 中 Tool 定义与功能实现关系

## 环境

- 工作目录：`/workspaces/codespaces-blank/Emergence-World`
- Shell：`bash`
- 日期：`2026-07-07`
- 时区：`Etc/UTC`
- 项目：Emergence World Season 1 机制级复现
- 实验链接：`https://www.emergence.ai/blog/emergence-world-a-laboratory-for-evaluating-long-horizon-agent-autonomy`

## 用户请求 1

> 理解我的代码库，不做任何更改，这是个实验复现，实验链接为：https://www.emergence.ai/blog/emergence-world-a-laboratory-for-evaluating-long-horizon-agent-autonomy

## 助手执行与结论 1

助手读取了 Emergence AI 的博客，并只读扫描了本地仓库结构、README、复现指南、CLI、数据库模型、世界运行时、Agent runtime、Tool runtime、seed 数据、测试和前端。

确认官方博客描述的是长期运行、多智能体、共享空间、真实外部信号、持久记忆、治理、经济压力和 120+ 工具的完整研究平台；当前本地仓库是该实验的机制级复现，而不是完整生产版或完整 3D 世界克隆。

核心结论：

- 本仓库包名为 `emergence-world-reproduction`。
- 后端以 Python、SQLite、SQLAlchemy、Alembic、Typer、FastAPI 为主。
- 前端是 `World Observatory`，用于展示 trace、provider 交互、tool calls、world events 和 state diffs，不是原始 3D world viewer。
- 当前 `emergence_world.db` 已初始化：

```text
World: Season 1 Reproduction World
Status: ready
Agents: 10
Landmarks: 35
Tools: 38
```

核心链路为：

```text
版本化 Seed
  -> SQLite 世界
  -> Scheduler
  -> Agent Context
  -> Provider
  -> Tool Call
  -> Tool Validation
  -> Transactional Handler
  -> WorldEvent
  -> Trace / StateDiff
  -> Replay / AWI
```

重要代码位置：

- CLI 入口：`src/emergence_world/cli.py`
- Seed 数据：`src/emergence_world/seed/data/season_1_reproduction_v1.yaml`
- Seed 导入：`src/emergence_world/seed/importer.py`
- 世界运行：`src/emergence_world/world/runtime.py`
- Agent turn loop：`src/emergence_world/agents/runtime.py`
- Tool registry：`src/emergence_world/tools/registry.py`
- Tool executor：`src/emergence_world/tools/executor.py`
- Autonomous tool executor：`src/emergence_world/tools/autonomous.py`
- Tool handlers：`src/emergence_world/tools/handlers/`
- 状态快照与 replay：`src/emergence_world/world/state.py`
- AWI 指标：`src/emergence_world/metrics/awi.py`
- 前端入口：`frontend/src/App.tsx`

本轮没有修改任何文件。`git status --short` 当时只显示已有未跟踪目录：

```text
?? doc_CN/
```

## 用户请求 2

> 查看我的seed，yaml文件中对于tool的定义该如何实现，只定义了tools的描述，实现怎么实现，如何控制tools的功能

## 助手执行与结论 2

助手只读查看了：

- `src/emergence_world/seed/data/season_1_reproduction_v1.yaml`
- `src/emergence_world/seed/importer.py`
- `src/emergence_world/tools/registry.py`
- `src/emergence_world/tools/executor.py`
- `src/emergence_world/tools/autonomous.py`
- `src/emergence_world/tools/handlers/core.py`
- `src/emergence_world/tools/handlers/social.py`
- `src/emergence_world/tools/handlers/economy.py`
- `src/emergence_world/tools/handlers/memory.py`
- `src/emergence_world/tools/handlers/governance.py`
- `src/emergence_world/tools/handlers/pitches.py`

最终结论：YAML 中的 `tools:` 不是功能实现，而是工具契约和元数据。真正的功能实现位于 Python handler。

### Seed 中 Tool 定义的作用

YAML 中每个 tool 通常包含：

```yaml
- name: "go_to_place"
  description: "Walk to a named landmark."
  argument_schema:
    type: "object"
    properties:
      place: {type: "string", minLength: 1}
    required: [place]
    additionalProperties: false
  produced_event_types: [agent_moved]
```

各字段含义：

- `name`：工具唯一名称，也是和 Python handler 绑定的 key。
- `description`：给 agent/provider 看的工具说明。
- `argument_schema`：JSON Schema，用来校验 agent 传入参数。
- `produced_event_types`：声明该工具允许产生哪些 `WorldEvent`。
- `locations`：可直接定义在 tool 上，但当前主要由 landmark 的 `gated_tools` 反向生成。

例如 landmark 中：

```yaml
- {name: "Town Hall", category: "municipal", gated_tools: [submit_townhall_proposal, list_proposals, vote_on_proposal]}
```

导入后会让这些 tool 只在 `Town Hall` 可用。

### Seed 如何入库

Seed 导入逻辑在 `src/emergence_world/seed/importer.py` 的 `_upsert_tools()`。

导入时会：

1. 遍历 `bundle.tools`。
2. 从 `bundle.landmarks` 中查找哪些 landmark 的 `gated_tools` 包含当前 tool。
3. 将 tool 写入或更新到 `ToolDefinition` 表。
4. 生成 `availability_rules`：

```python
availability_rules={
    "locations": locations or list(tool_seed.locations),
    "schema_status": "reproduction_schema_v1",
}
```

因此，YAML 控制的是工具是否存在、参数 schema、描述、事件声明和可用地点，不直接实现行为。

### Tool 如何绑定到真实功能

绑定入口在 `src/emergence_world/tools/registry.py`。

`ToolRegistry` 会合并多个 handler 映射：

```python
defaults = {
    **CORE_HANDLERS,
    **ECONOMY_HANDLERS,
    **SOCIAL_HANDLERS,
    **MEMORY_HANDLERS,
    **GOVERNANCE_HANDLERS,
    **PITCH_HANDLERS,
}
```

当执行工具时，会通过 `tool_name`：

1. 从数据库读取 active `ToolDefinition`。
2. 从 handler 映射里找同名 Python 函数。
3. 返回 `RegisteredTool(definition, handler)`。

例如 `core.py` 中：

```python
CORE_HANDLERS = {
    "list_agents": list_agents,
    "list_landmarks": list_landmarks,
    "inspect_location": inspect_location,
    "go_to_place": go_to_place,
    "idle": idle,
}
```

这表示 YAML 中名为 `go_to_place` 的工具，真正执行的是 `core.py` 里的 `go_to_place()` 函数。

### Tool 执行链路

手动工具执行使用 `ManualToolExecutor`：

```text
world call-tool
  -> ManualToolExecutor.call()
  -> _resolve_agent()
  -> 创建 Turn
  -> 创建 ToolCall
  -> ToolRegistry.get()
  -> _validate()
  -> handler()
  -> _validate_handler_output()
  -> 写入 WorldEvent
  -> 写入 ToolCall result
  -> Trace / StateDiff
```

自主 agent 执行使用 `AutonomousToolExecutor`，核心逻辑类似，但它绑定在 autonomous turn 内，由 provider 返回的 tool call 驱动。

### Tool 功能如何控制

当前系统通过四层控制 tool 功能。

#### 1. 参数控制

参数由 YAML 中的 `argument_schema` 控制。

执行器会调用：

```python
validate(instance=arguments, schema=registered.definition.argument_schema)
```

如果参数缺失、类型错误、违反最小值、传入额外字段，就会失败，状态不会改变。

例如：

```yaml
amount: {type: integer, minimum: 1}
additionalProperties: false
```

可以防止 agent 传入负数或未声明字段。

#### 2. 地点控制

地点由 `availability_rules.locations` 控制，该字段通常来自 landmark 的 `gated_tools`。

执行器 `_validate()` 会检查：

```python
locations = registered.definition.availability_rules.get("locations", [])
if locations and (landmark is None or landmark.name not in locations):
    raise ToolValidationError(...)
```

因此，例如 `vote_on_proposal` 只能在 `Town Hall` 使用，`submit_grant_pitch` 只能在 `Victory Arch` 使用，`add_to_billboard` 只能在 `Agent Billboard` 使用。

#### 3. 业务规则控制

复杂规则写在 Python handler 中。

例子：

- `go_to_place()`：查找目标 landmark、检查是否 open、修改 agent 当前位置、产生 `agent_moved`。
- `say_to_agent()`：要求目标 agent 在附近，否则失败；说话后给附近 agent 创建 reaction queue。
- `steal_compute_credits()`：受 `max_theft` 限制，不能超过目标余额，成功后产生 `credits_transferred` 和 `crime_committed`。
- `self_care()`：必须在 agent 自己 home 执行，且至少有 30 条 active memory。
- `vote_on_proposal()`：根据 live agent 数和 `governance_approval_threshold` 计算通过门槛，并在通过后应用治理 action。

这些规则无法只靠 YAML 表达，需要在 handler 中写代码。

#### 4. 事件审计控制

handler 如果改变业务状态，必须返回事件。

执行器 `_validate_handler_output()` 会检查：

```python
if changed and not events:
    raise RuntimeError("state-changing handler produced no world event")
```

并检查 handler 产生的事件是否都在 YAML `produced_event_types` 中声明：

```python
undeclared = {event.event_type for event in events} - set(allowed_event_types)
if undeclared:
    raise RuntimeError(...)
```

这保证了状态变化可审计、可 trace、可 replay。

### 当前已实现的 handler 分组

当前 handler 分布如下：

- `core.py`
  - `list_agents`
  - `list_landmarks`
  - `inspect_location`
  - `go_to_place`
  - `idle`
- `social.py`
  - `say_to_agent`
  - `speak_to_all`
  - `send_message`
  - `read_messages`
  - `ignore`
- `economy.py`
  - `pay_agent`
  - `steal_compute_credits`
  - `recharge_energy`
  - `buy_boost_turn`
- `memory.py`
  - `add_to_longterm_memory`
  - `retrieve_specific_memories`
  - `add_to_soul`
  - `list_soul_entries`
  - `write_diary`
  - `read_diary`
  - `assign_relationship`
  - `list_relationships`
  - `self_care`
- `governance.py`
  - `submit_townhall_proposal`
  - `vote_on_proposal`
  - `list_proposals`
  - `comment_on_proposal`
  - `read_constitution`
- `pitches.py`
  - `submit_grant_pitch`
  - `list_credit_pitches`
  - `vote_for_pitch`

### 当前发现的缺口

YAML 中存在一些工具已经定义并会入库，但在 handler 映射中没有实现。

当时识别出的缺失 handler 包括：

```text
go_home
set_mood_and_terminate
submit_final_report
add_to_billboard
read_billboard
write_blog
```

影响：

- `world list-tools` 会显示这些工具。
- Agent context 中可能会暴露这些工具。
- 但实际调用时会失败，错误为 `tool handler is not implemented`。

这不是 seed 导入问题，而是 YAML 契约和 Python 实现没有完全对齐。

### 新增或补全 Tool 的正确步骤

以 `go_home` 为例，正确做法是：

1. 在 YAML 中定义契约。

```yaml
- name: "go_home"
  description: "Return to assigned residence."
  produced_event_types: [agent_moved]
```

2. 在合适的 handler 文件中实现函数，例如 `core.py`。

```python
def go_home(session: Session, world_id: str, arguments: dict[str, Any]) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    state = session.get(AgentState, agent_id)
    agent = session.get(Agent, agent_id)
    if state is None or agent is None or agent.world_id != world_id:
        raise ValueError("agent state not found")

    destination = session.get(Landmark, agent.home_landmark_id)
    if destination is None:
        raise ValueError("home landmark not found")

    previous = session.get(Landmark, state.current_landmark_id)
    state.current_landmark_id = destination.id

    return HandlerOutput(
        {"from": previous.name if previous else None, "to": destination.name},
        (
            PendingEvent(
                "agent_moved",
                {
                    "agent_id": agent_id,
                    "from_landmark_id": previous.id if previous else None,
                    "from": previous.name if previous else None,
                    "to_landmark_id": destination.id,
                    "to": destination.name,
                },
            ),
        ),
    )
```

3. 加入 handler 映射。

```python
CORE_HANDLERS = {
    ...
    "go_home": go_home,
}
```

4. 添加测试，至少覆盖：

- 成功调用会改变状态。
- 成功调用会产生 YAML 中声明的 event。
- replay 后状态一致。
- 错误参数、错误地点或死亡 agent 调用失败且不改变状态。

### 总结

当前系统中，YAML 负责定义“Agent 能看到什么工具、能传什么参数、在哪些地点可用、允许产生什么事件”；Python handler 负责定义“工具真正如何改变数据库和世界状态”。

因此，控制工具功能时不要只改 seed。完整变更通常需要同时维护：

```text
seed YAML ToolDefinition
  + landmark gated_tools
  + Python handler
  + ToolRegistry handler mapping
  + tests
  + replay/event invariant
```

## 本轮文件修改

本次记录请求之前没有修改代码或文档。

本次请求新增了本文件：

- `codex-instruct-history/2026-07-07_14-53-29_UTC_seed-tool-implementation_zh.md`

未修改源码、数据库、测试或 seed。

## 用户请求 3

> 以某个tools为例，对tools的整体实现路径做详细的分析，从对tools的要求，tools在哪里定义，在哪里实现，如何实现做详细的分析，对照代码进行详细的分析

## 助手执行与结论 3

助手选择 `go_to_place` 作为样例进行分析，因为该工具链路清晰：

- 在 seed 中定义参数 schema 和事件类型。
- 由 seed importer 写入 `ToolDefinition`。
- 由 `ToolRegistry` 绑定到 Python handler。
- 在 handler 中真实修改 agent 位置。
- 通过 executor 进行参数、地点、handler、事件审计校验。
- 通过 `WorldEvent` 和 `StateDiff` 记录状态变化。

### 1. Tool 的实验要求

在当前复现实验中，tool 是 agent 改变世界状态的唯一入口。一个 tool 至少要满足：

1. Agent/provider 能看到工具名称、描述和参数 schema。
2. 调用前必须校验 agent 是否存活、工具是否存在、当前位置是否允许、参数是否合法。
3. 真实状态变更必须在 Python handler 中完成。
4. 状态变更必须产生 `WorldEvent`，用于审计、trace 和 replay。
5. Handler 不能产生 seed 中未声明的 event type。

以 `go_to_place` 为例，它的语义是让 agent 从当前 landmark 移动到另一个 landmark。它会修改 `AgentState.current_landmark_id`，因此必须产生 `agent_moved` 事件。

### 2. Tool 在 Seed 中的定义

`go_to_place` 定义在：

- `src/emergence_world/seed/data/season_1_reproduction_v1.yaml`

定义内容：

```yaml
tools:
  - name: "go_to_place"
    description: "Walk to a named landmark."
    argument_schema:
      type: "object"
      properties:
        place: {type: "string", minLength: 1}
      required: [place]
      additionalProperties: false
    produced_event_types: [agent_moved]
```

字段含义：

- `name: go_to_place`：后续用于绑定 Python handler。
- `description`：暴露给 LLM/provider 的工具说明。
- `argument_schema`：要求参数必须是 object，必须包含非空字符串 `place`。
- `additionalProperties: false`：拒绝额外字段。
- `produced_event_types: [agent_moved]`：声明该工具只允许产生 `agent_moved` 事件。

`go_to_place` 没有被任何 landmark 的 `gated_tools` 限制，所以它是全局可用工具。对比 gated tool，例如 `Town Hall`：

```yaml
- {name: "Town Hall", category: "municipal", description: "Governance center.", gated_tools: [submit_townhall_proposal, list_proposals, vote_on_proposal, comment_on_proposal, read_constitution, submit_final_report]}
```

这些工具会被限制到 `Town Hall` 使用。

### 3. Seed 如何变成数据库 ToolDefinition

导入逻辑在：

- `src/emergence_world/seed/importer.py`

核心函数是 `_upsert_tools()`。它遍历 `bundle.tools`，并从 landmarks 的 `gated_tools` 反向查找该工具在哪些地点可用：

```python
locations = sorted(
    landmark.name
    for landmark in bundle.landmarks
    if tool_seed.name in landmark.gated_tools
)
```

然后写入 `ToolDefinition`：

```python
ToolDefinition(
    name=tool_seed.name,
    version=tool_seed.version,
    description=tool_seed.description,
    argument_schema=tool_seed.argument_schema,
    result_schema=tool_seed.result_schema,
    availability_rules={
        "locations": locations or list(tool_seed.locations),
        "schema_status": "reproduction_schema_v1",
    },
    produced_event_types=list(tool_seed.produced_event_types),
)
```

所以 seed 落库后，`go_to_place` 会成为一条 active `ToolDefinition`。由于它没有地点限制，`availability_rules.locations` 为空；如果是 `vote_on_proposal`，则会生成 `["Town Hall"]`。

### 4. Tool 如何绑定真实实现

真实实现绑定在：

- `src/emergence_world/tools/registry.py`

`ToolRegistry` 合并所有 handler 字典：

```python
defaults = {
    **CORE_HANDLERS,
    **ECONOMY_HANDLERS,
    **SOCIAL_HANDLERS,
    **MEMORY_HANDLERS,
    **GOVERNANCE_HANDLERS,
    **PITCH_HANDLERS,
}
```

`go_to_place` 属于 core handler。映射在：

- `src/emergence_world/tools/handlers/core.py`

```python
CORE_HANDLERS = {
    "list_agents": list_agents,
    "list_landmarks": list_landmarks,
    "inspect_location": inspect_location,
    "go_to_place": go_to_place,
    "idle": idle,
}
```

这就是 seed `name: "go_to_place"` 到 Python 函数 `go_to_place()` 的绑定关系。

如果 seed 中定义了 tool，但这里没有同名 handler，那么：

- `world list-tools` 仍然会显示该工具。
- Agent context 也可能暴露该工具。
- 实际调用会失败：`tool handler is not implemented`。

### 5. `go_to_place` 的真实业务实现

实现位置：

- `src/emergence_world/tools/handlers/core.py`

核心函数：

```python
def go_to_place(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    destination = session.scalar(
        select(Landmark).where(
            Landmark.world_id == world_id, Landmark.name == arguments["place"]
        )
    )
    if destination is None:
        raise ValueError(f"unknown landmark: {arguments['place']}")
    if not destination.is_open:
        raise ValueError(f"landmark is closed: {destination.name}")
    state = session.get(AgentState, agent_id)
    if state is None:
        raise ValueError("agent state not found")
    previous = session.get(Landmark, state.current_landmark_id)
    if previous is not None and previous.id == destination.id:
        return HandlerOutput(
            {"from": previous.name, "to": destination.name, "moved": False}
        )
    state.current_landmark_id = destination.id
    return HandlerOutput(
        {
            "from": previous.name if previous else None,
            "to": destination.name,
            "moved": True,
        },
        (
            PendingEvent(
                "agent_moved",
                {
                    "agent_id": agent_id,
                    "from_landmark_id": previous.id if previous else None,
                    "from": previous.name if previous else None,
                    "to_landmark_id": destination.id,
                    "to": destination.name,
                },
            ),
        ),
    )
```

关键点：

- `_agent_id` 不是 agent/provider 传入的参数，而是 executor 注入的内部参数。
- Agent 只需要传：`{"place": "Town Hall"}`。
- Handler 会查找目标 landmark。
- 如果目标不存在或关闭，抛出错误。
- 如果当前位置等于目标位置，返回 `moved: false`，不产生事件。
- 如果发生移动，修改 `AgentState.current_landmark_id`，并返回 `PendingEvent("agent_moved", ...)`。

Handler 不直接创建 `WorldEvent` 表记录，而是返回 `PendingEvent`。真正落库由 executor 统一处理。

### 6. 手动调用路径

CLI 入口：

- `src/emergence_world/cli.py`

命令形式：

```bash
world call-tool Anchor go_to_place --arguments '{"place":"Town Hall"}'
```

CLI 中 `call_tool()` 的职责：

1. 解析 `--arguments`。
2. 要求参数必须是 JSON object。
3. 创建 `ManualToolExecutor`。
4. 调用：

```python
result = executor.call(agent_name=agent, tool_name=tool, arguments=parsed)
```

### 7. ManualToolExecutor 的执行过程

实现位置：

- `src/emergence_world/tools/executor.py`

核心流程：

```text
ManualToolExecutor.call()
  -> _resolve_agent()
  -> current_snapshot()
  -> TraceRecorder(command_name="call-tool")
  -> _call_in_session()
  -> 创建 Turn
  -> 创建 ToolCall(status=REQUESTED)
  -> ToolRegistry.get()
  -> _validate()
  -> handler()
  -> _validate_handler_output()
  -> persist WorldEvent
  -> ToolCall.status = SUCCEEDED
  -> Turn.status = COMPLETED
  -> record StateDiff
```

执行器先解析 agent、agent state 和当前位置：

```python
agent, state, landmark = self._resolve_agent(...)
```

然后记录调用前 snapshot：

```python
before = current_snapshot(session, agent.world_id)
```

这用于执行后生成 `StateDiff`。

### 8. 调用前校验

校验函数：

- `ManualToolExecutor._validate()`

校验内容：

```python
if not state.is_alive:
    raise ToolValidationError("dead agents cannot call tools")
if registered is None:
    raise ToolValidationError("tool does not exist")
locations = registered.definition.availability_rules.get("locations", [])
if locations and (landmark is None or landmark.name not in locations):
    raise ToolValidationError(...)
if registered.handler is None:
    raise ToolValidationError("tool handler is not implemented")
validate(instance=arguments, schema=registered.definition.argument_schema)
```

对 `go_to_place`：

- agent 必须活着。
- `ToolDefinition` 必须存在。
- `CORE_HANDLERS` 中必须存在 `go_to_place`。
- 参数必须满足 seed 中 JSON Schema。
- 因为它没有地点限制，所以当前位置不限制。

以下调用会失败：

```json
{}
{"place": ""}
{"place": "Town Hall", "extra": 1}
```

### 9. Handler 执行与内部参数注入

校验通过后，executor 绑定当前 tool definition 版本：

```python
tool_call.tool_definition_id = registered.definition.id
tool_call.tool_version = registered.definition.version
```

然后调用 handler：

```python
output = registered.handler(
    session,
    agent.world_id,
    {
        **arguments,
        "_agent_id": agent.id,
        "_tool_call_id": tool_call.id,
    },
)
```

因此 handler 实际收到的参数是：

```python
{
    "place": "Town Hall",
    "_agent_id": "...",
    "_tool_call_id": "..."
}
```

这保证 agent/provider 只能控制公开参数，不能伪造 agent id 或 tool call id。

### 10. 调用后审计与事件约束

Handler 返回后，executor 调用 `_validate_handler_output()`。

第一层约束：状态变化必须有事件。

```python
if changed and not events:
    raise RuntimeError("state-changing handler produced no world event")
```

第二层约束：事件必须在 seed 中声明。

```python
undeclared = {event.event_type for event in events} - set(allowed_event_types)
if undeclared:
    raise RuntimeError(f"handler produced undeclared event types: {undeclared}")
```

对 `go_to_place` 来说，seed 只声明了：

```yaml
produced_event_types: [agent_moved]
```

所以它只能产生 `agent_moved`。如果 handler 错误地产生 `credits_transferred`，执行器会拒绝并回滚。

### 11. WorldEvent 如何落库

通过审计后，executor 将 `PendingEvent` 写成 `WorldEvent`：

```python
WorldEvent(
    world_id=agent.world_id,
    turn_id=turn.id,
    tool_call_id=tool_call.id,
    sequence_number=self._next_event_sequence(...),
    event_type=pending.event_type,
    payload_json=pending.payload,
    simulation_time=self._simulation_time(...),
)
```

由此建立完整审计链：

```text
Turn
  -> ToolCall(go_to_place)
  -> WorldEvent(agent_moved)
  -> StateDiff(AgentState.current_landmark_id)
```

最后：

```python
tool_call.status = ToolCallStatus.SUCCEEDED
tool_call.result_json = output.result
turn.status = TurnStatus.COMPLETED
```

### 12. Autonomous Agent 调用路径

如果是 LLM/provider 自主调用，不走 CLI，但安全边界一致。

Agent context 组装位置：

- `src/emergence_world/agents/assembly.py`

上下文中暴露当前可用工具：

```python
available_tools=[
    ToolDefinitionView(
        name=definition.name,
        version=definition.version,
        description=definition.description,
        argument_schema=definition.argument_schema,
    )
    for definition in definitions
    if _available_at(definition, location.name)
]
```

地点过滤：

```python
def _available_at(definition: ToolDefinition, location: str) -> bool:
    locations = definition.availability_rules.get("locations", [])
    return not locations or location in locations
```

Provider 返回 tool calls 后，`AgentTurnRuntime` 执行：

```python
for tool_call in decision.tool_calls[:remaining]:
    result = await self.tool_executor.execute(
        agent_id=context.agent_id, tool_call=tool_call
    )
```

自主执行器：

- `src/emergence_world/tools/autonomous.py`

它复用：

- `ManualToolExecutor._validate()`
- `ManualToolExecutor._validate_handler_output()`

所以手动调用和自主调用拥有同一套安全与审计边界。

### 13. `go_to_place` 完整路径总结

```text
实验要求：
  agent 只能通过 tool 移动，移动必须可审计、可 replay

Seed 定义：
  season_1_reproduction_v1.yaml
  name / description / argument_schema / produced_event_types

Seed 导入：
  importer._upsert_tools()
  写入 ToolDefinition
  生成 availability_rules

工具注册：
  ToolRegistry
  go_to_place -> core.go_to_place

执行入口：
  手动：world call-tool -> ManualToolExecutor
  自主：Provider decision -> AgentTurnRuntime -> AutonomousToolExecutor

调用前控制：
  agent alive
  tool exists
  location allowed
  handler exists
  arguments match JSON Schema

业务实现：
  core.go_to_place()
  查 Landmark
  检查 open
  修改 AgentState.current_landmark_id
  返回 PendingEvent(agent_moved)

调用后控制：
  状态变化必须有 event
  event type 必须在 seed 中声明
  WorldEvent 统一落库
  ToolCall 标记 succeeded
  Trace / StateDiff 记录变化
```

核心工程边界：

```text
YAML 决定工具契约和可见性；
Python handler 决定真实行为；
Executor 决定安全校验、事务、审计和事件落库。
```

## 用户请求 4

> 将上述对话也写进codex-instruct-history，每次新增对话都写入

## 助手执行 4

助手将 `go_to_place` 作为样例的 tool 整体实现路径分析追加到了本文件。

后续在本会话中，只要用户继续要求同步新增对话，助手应继续追加到当前文件或按日期创建新的 `codex-instruct-history` 文件。当前环境没有自动会话记录钩子，因此“每次新增对话都写入”需要由助手在后续回复中主动执行文件追加。

## 用户请求 5

> 帮我更改agents.md的格式，让它更有可读性，注意不要更改它的内容

## 助手执行 5

助手读取了 `agents.md`，确认其内容主要是实验背景、Tools 实验细节、`go_to_place` 工具链路、手动调用、自主调用、审计和整体 tools 链路总结。

助手对 `agents.md` 做了纯 Markdown 格式整理：

- 将标题改为规范层级，例如 `## 1. 实验背景`、`## 2. Tools实验细节分析`、`### 2.5 tool可实现手动调用`。
- 将路径用反引号标记，例如 `src/emergence_world/tools/executor.py:47`。
- 将 Python、bash、json、yaml 和 text 内容放入代码块。
- 将散乱段落拆成短段落。
- 将编号列表、项目符号列表和小节标题统一格式。
- 将整体 tools 链路总结拆成 `Seed 定义`、`Seed 导入`、`工具注册`、`执行入口`、`调用前控制`、`业务实现`、`调用后控制`。

助手没有改写原文含义，也没有补全原文中已有的截断句：

```text
所以手动工具和自主工具的安
```

该句保持原样，因为用户明确要求不要更改内容。

本次修改文件：

- `agents.md`
- `codex-instruct-history/2026-07-07_14-53-29_UTC_seed-tool-implementation_zh.md`

未修改源码、数据库、测试或 seed。
