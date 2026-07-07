# 背景介绍及代码细节分析

本文档记录了代码库的实验背景，目的，以及实验的细节

## 1. 实验背景

本代码库是对以下实验的复现：

https://www.emergence.ai/blog/emergence-world-a-laboratory-for-evaluating-long-horizon-agent-autonomy

官方git仓库为：

https://github.com/EmergenceAI/Emergence-World

## 2. Tools实验细节分析

tool 不是简单函数，而是 agent 改变世界状态的唯一入口。对一个 tool 的要求大致有五类：

1. Agent/provider 能看到它的名称、描述和参数 schema。
2. 调用前必须校验 agent 是否活着、工具是否存在、当前位置是否允许、参数是否合法。
3. 真正的状态变更必须在 Python handler 中完成。
4. 状态变更必须产生 WorldEvent，用于审计、trace 和 replay。
5. handler 不能产生 seed 中未声明的 event type。

### 2.1 实验 seed 路径

`src/emergence_world/seed/data/season_1_reproduction_v1.yaml`

### 2.2 tools 功能实现路径

`src/emergence_world/tools/handlers/`

注册入口是：

`src/emergence_world/tools/registry.py`

对于tools的要求：

- 成功调用会改变状态。
- 产生声明过的 event。
- replay 后状态一致。
- 错误参数/错误地点/死亡 agent 会失败且不改状态。

### 2.3 工具有门控

工具有门控：gated_tools，即有些工具会限制使用地点，只有agent到达该地点才能使用工具

### 2.4 tool 实现路径

#### Seed变成数据库中的 ToolDefinition

导入逻辑在 `src/emergence_world/seed/importer.py:215`。

`_upsert_tools()` 会遍历 seed 中的 `bundle.tools`，并从 landmarks 的 `gated_tools` 反查这个工具在哪些地点可用,然后写入 ToolDefinition

#### Tool绑定真实实现

真实实现绑定在 `src/emergence_world/tools/registry.py:29`

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

举例go_to_place:seed中定义的tool name对应的函数映射在 `src/emergence_world/tools/handlers/core.py`，具体实现见相应的函数

```python
CORE_HANDLERS = {
    "list_agents": list_agents,
    "list_landmarks": list_landmarks,
    "inspect_location": inspect_location,
    "go_to_place": go_to_place,
    "idle": idle,
}
```

Tool实现逻辑：分析具体函数 Todo

### 2.5 tool可实现手动调用

CLI 入口是 `src/emergence_world/cli.py:288`：

```bash
world call-tool Anchor go_to_place --arguments '{"place":"Town Hall"}'
```

CLI 做两件事：

1. 解析 `--arguments`，要求它是 JSON object。
2. 创建 ManualToolExecutor 并调用：

```python
result = executor.call(agent_name=agent, tool_name=tool, arguments=parsed)
```

真正执行在 `src/emergence_world/tools/executor.py:47`。

执行器先解析 agent 当前状态和当前位置：

```python
agent, state, landmark = self._resolve_agent(...)
```

然后记录调用前 snapshot，用于后续 state diff：

```python
before = current_snapshot(session, agent.world_id)
```

之后创建 trace、turn、tool_call。ToolCall 初始状态是 REQUESTED，见 `src/emergence_world/tools/executor.py:105`。

### 2.6 工具调用前进行校验

校验入口在 `src/emergence_world/tools/executor.py:119`：

```python
registered = self._registry.get(session, tool_name)
self._validate(registered, state, landmark, arguments)
```

`_validate()` 在 `src/emergence_world/tools/executor.py:263`，控制四件事：

1. 死亡 agent 不能调用工具：

```python
if not state.is_alive:
    raise ToolValidationError("dead agents cannot call tools")
```

2. 工具必须存在：

```python
if registered is None:
    raise ToolValidationError("tool does not exist")
```

3. 如果工具有地点限制，当前 landmark 必须匹配：

```python
locations = registered.definition.availability_rules.get("locations", [])
if locations and (landmark is None or landmark.name not in locations):
    raise ToolValidationError(...)
```

对 go_to_place 来说，locations 为空，所以全局可用。对 vote_on_proposal 来说，只有在 Town Hall 才会通过。

4. 必须有 handler：

```python
if registered.handler is None:
    raise ToolValidationError("tool handler is not implemented")
```

5. 参数必须符合 seed 中的 JSON Schema：

```python
validate(instance=arguments, schema=registered.definition.argument_schema)
```

所以这些调用会失败：

```json
{}
{"place": ""}
{"place": "Town Hall", "extra": 1}
```

### 2.7 Handler执行

校验通过后，executor 把数据库中的 tool definition 版本绑定到本次 ToolCall

```python
tool_call.tool_definition_id = registered.definition.id
tool_call.tool_version = registered.definition.version
```

然后在 nested transaction 里执行 handler，见 `src/emergence_world/tools/executor.py:153`：

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

这里会把 agent 原始参数扩展成：

```json
{
    "place": "Town Hall",
    "_agent_id": "...",
    "_tool_call_id": "..."
}
```

这样 handler 能知道是谁调用了工具、本次 tool call id 是什么，但这些内部字段不暴露给 LLM。

### 2.8 如何保证状态变化可审计

handler 返回后，executor 会调用 `src/emergence_world/tools/executor.py:308` 的 `_validate_handler_output()`：

```python
changed = [
    item
    for item in set(session.new) | set(session.dirty) | set(session.deleted)
    if not isinstance(item, audit_models)
]
if changed and not events:
    raise RuntimeError("state-changing handler produced no world event")
```

含义：如果 handler 改了业务状态，例如 AgentState.current_landmark_id，却没有返回任何事件，就报错。

接着检查事件类型是否在 seed 中声明：

```python
undeclared = {event.event_type for event in events} - set(allowed_event_types)
if undeclared:
    raise RuntimeError(...)
```

对 go_to_place 来说，seed 只声明了：

```yaml
produced_event_types: [agent_moved]
```

所以 handler 只能产生 agent_moved。如果它错误地产生 credits_transferred，执行器会拒绝并回滚。

### 2.9 WorldEvent 如何落库

通过校验后，executor 统一把 PendingEvent 写成 WorldEvent，见 `src/emergence_world/tools/executor.py:181`：

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

这一步把业务行为和审计记录串起来：

```text
Turn
  -> ToolCall
  -> WorldEvent(agent_moved)
  -> StateDiff
```

最后 ToolCall 标记成功，写入 result：

```python
tool_call.status = ToolCallStatus.SUCCEEDED
tool_call.result_json = output.result
turn.status = TurnStatus.COMPLETED
```

### 2.10 Autonomous Agent 调用路径

如果不是手动 CLI，而是 LLM/provider 自主调用，路径稍有不同。

agent context 构建时会把当前可用工具暴露给 provider，见 `src/emergence_world/agents/assembly.py:157`：

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

地点过滤在 `src/emergence_world/agents/assembly.py:179`：

```python
locations = definition.availability_rules.get("locations", [])
return not locations or location in locations
```

所以 LLM 只能看到当前位置允许的工具。go_to_place 没有地点限制，所以总能看到；vote_on_proposal 只有在 Town Hall 才会出现在 available_tools 里。

provider 返回 tool calls 后，AgentTurnRuntime 会逐个执行，见 `src/emergence_world/agents/runtime.py:137`：

```python
for tool_call in decision.tool_calls[:remaining]:
    result = await self.tool_executor.execute(...)
```

自主执行器是 `src/emergence_world/tools/autonomous.py:24`。它复用 `ManualToolExecutor._validate()` 和 `_validate_handler_output()`， 所以手动工具和自主工具的安全边界一致， 含义是：无论 tool 是通过 CLI 手动调用，还是由 LLM/provider 在 autonomous turn 中调用，都会经过同一套工具存在性、地点、参数 schema、handler 存在性和事件审计校验。

## 3. 整体tools链路总结

agent 只能通过 tool 移动，移动必须可审计、可 replay

### Seed 定义

```text
season_1_reproduction_v1.yaml
name / description / argument_schema / produced_event_types
```

### Seed 导入

```text
importer._upsert_tools()
写入 ToolDefinition
生成 availability_rules
```

### 工具注册

```text
ToolRegistry
go_to_place -> core.go_to_place
```

### 执行入口

```text
手动：world call-tool -> ManualToolExecutor
自主：Provider decision -> AgentTurnRuntime -> AutonomousToolExecutor
```

### 调用前控制

```text
agent alive
tool exists
location allowed
handler exists
arguments match JSON Schema
```

### 业务实现

```text
core.go_to_place()
查 Landmark
检查 open
修改 AgentState.current_landmark_id
返回 PendingEvent(agent_moved)
```

### 调用后控制

```text
状态变化必须有 event
event type 必须在 seed 中声明
WorldEvent 统一落库
ToolCall 标记 succeeded
Trace / StateDiff 记录变化
```

最关键的工程边界是：YAML 决定工具契约和可见性，Python handler 决定真实行为，executor 决定安全校验、事务、审计和事件落库。
