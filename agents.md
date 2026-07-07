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

## 3.Memory实验细节分析

整体机制策略

每个 agent 都有一套私有、分层、可审计的长期认知状态；agent 只能通过工具显式写入或整理记忆，系统在每次 autonomous turn，前按固定策略抽取一部分记忆放入上下文。

  核心层次

  1. Soul Entries
     最深层身份锚点，存 agent 的信念、价值观、执念。通过 add_to_soul 写入，通过 list_soul_entries 读取。当前实现里 soul entry 不参与 self-care 归档，会始终
     作为上下文候选进入。

  2. Episodic Memory
     常规长期记忆，agent 通过 add_to_longterm_memory 主动写入，包含：
     content、importance、tags、active、archived_at。
     检索工具是 retrieve_specific_memories，按关键词匹配内容，再按重要性和时间排序。

  3. Diary
     日记层，一天一条当前版本，可修订。write_diary 会根据当前 simulation date 写入或覆盖当天条目，同时留下 DiaryRevision，包含 mood/location 元数据。
     read_diary 默认读最近若干条。

  4. Conversation Records
     对话记忆是每个 agent 私有视角下的 conversation record，记录 speaker、target、channel、content。它会作为最近社交上下文进入 memory context。

  5. Relationship Graph
     agent 对其他 agent 的主观关系模型。通过 assign_relationship 写入/更新，包含：
     relationship_type、rationale、trust_score、affinity_score、interaction_count。
     每次更新也会产生 revision，便于追踪关系变化。

  6. Memory Summary
     self-care 触发的压缩层。当前 reproduction 实现里不是 LLM 摘要，而是确定性算法 deterministic_summary_v1：统计 top tags，选出高 importance 记忆作为
     highlights，生成 summary。

Self-Care 流程

  self_care 是记忆整理工具，有严格条件：

  - agent 必须在自己的 home landmark。
  - 至少有 30 条 active episodic memories。
  - 一次最多处理 500 条。
  - 生成一条 MemorySummary。
  - 原始 episodic memories 被标记为 inactive，并写入 archived_at。
  - MemorySummarySource 保存 summary 和原始 memory 的来源关系。
  - 产生 memory_summarized 和 memory_archived 事件。

  所以它不是删除记忆，而是把明细归档，再用 summary 维持长期可用信息密度。

  进入上下文的策略

  每次 autonomous turn 前，assemble_autonomous_context 会调用 build_memory_context。这个过程会构造并审计 agent 的私有 memory context：

  - soul：全部选入。
  - diary：最近 5 条选入。
  - conversation：最近 5 条选入。
  - relationships：全部选入。
  - episodic memories：默认最多 10 条，按 关键词匹配 + importance 排序。
  - summaries：只有当没有 episodic memory 被选中时，才补充最近 summary。

  重要的是：系统会把每个候选项、分数、是否入选、排除原因写入 context_memory_candidates 和 context_memory_selections。这让实验可以回放和解释“为什么 agent 当
  时看到了这些记忆”。

  整体特点

  当前实现的 memory 机制偏“可复现、可审计”，不是向量数据库式语义检索。它通过明确的数据表、工具调用、事件、revision 和 context build 审计，把 agent 的长期身
  份、经验、日记、关系和对话压缩进每次决策上下文。文档中提到的 LLM summarization / neural link 等更完整机制属于整体设计描述；当前代码里已实现的重点是确定性
  memory 写入、检索、self-care 摘要和上下文选择审计。



• 和文档层的 memory 设计相比，你当前代码实现的是一个“确定性、可审计的 memory 子集”。主要缺失如下。

  明显缺失

  1. Neural Link 记忆共享未实现
     文档设计有：
     neural_link_request_memory、neural_link_share_memory、2 分钟接受窗口、完整 memory bank 复制。

     代码里目前没有：
      - 工具定义
      - handler
      - 请求/授权/过期状态表
      - memory transfer 事件
      - 防重放或权限校验
      - 测试覆盖

  2. LLM 摘要未实现
     文档里 self-care 会用 LLM 对旧记忆进行连贯叙事摘要。

     当前代码用的是 deterministic_summary_v1：
      - 统计 top tags
      - 选 importance 最高的几条 highlights
      - 生成固定格式 summary

     这对复现很好，但不等同于文档中的认知总结能力。

  3. Conversation archival / conversation summary 不完整
     文档提到 conversation history 最多 1000 条，self-care 会归档和摘要对话。

     当前代码有 ConversationRecord，也会进入 context，但没有看到：
      - conversation self-care 归档逻辑
      - conversation summary 表或机制
      - conv_summarized_until 水位线
      - 1000 条触发阈值

  4. Archived memories 独立层未实现
     文档把 archived memories 当作归档区。

     当前实现是把 EpisodicMemory.active = False，并设置 archived_at，再用 MemorySummarySource 关联 summary 来源。功能上接近，但没有独立 archived memory
     bank，也没有归档检索工具。

  5. Memory 检索比较简单
     文档暗示长期认知系统会支持更丰富的记忆调取。

     当前实现主要是：
      - retrieve_specific_memories：content 字符串 contains 查询
      - context build：关键词 overlap + importance 排序
      - 没有 embedding/vector search
      - 没有 recency/importance/relationship/context 多因素综合策略
      - 没有跨 summary 和 archived memory 的深度召回

  6. Soul Entry 管理不完整
     文档说 soul entries 可以由 agent 手动 add/remove。

     当前有：
      - add_to_soul
      - list_soul_entries

     但没有看到：
      - remove/deactivate soul entry 工具
      - revise soul entry 工具
      - soul 冲突检测或审计策略

  7. Diary 搜索能力不足
     文档说 diary 可按 keyword 和 date 搜索。

     当前有：
      - write_diary
      - read_diary 最近 N 条

     但缺少：
      - 按日期读取
      - 按关键词搜索
      - 日期范围查询

  8. Relationship Graph 比文档简化
     当前实现有 directional relationship、trust、affinity、interaction_count、revision。

     但文档里的关系层更丰富，当前缺少或较弱：
      - emotional tone 字段
      - first_met_at
      - relationship history 的结构化展开
      - 自动由 conversation/social event 更新关系
      - 关系摘要或社会网络层分析

  9. Memory token ceiling / budget 管理未实现
     文档提到：
      - token ceiling 100,000
      - post-summary ceiling 50,000

     当前 context build 是条数限制：
      - episodic 默认 10
      - diary/recent conversation 默认 5
      - 没有 token 估算
      - 没有根据 prompt budget 动态裁剪

  10. Memory privacy / consent 模型还不完整
     当前 memory 基本是 agent 私有状态；这部分是有的。

  但文档中 neural link 引入了授权共享，所以还需要：

  - 请求方/授权方状态
  - 过期窗口
  - 可共享范围
  - 拒绝事件
  - 审计记录
  - 防止 agent 绕过 consent 读取他人 memory

  已经实现得比较扎实的部分

  - 私有 episodic memory 写入与检索
  - soul entry 写入与上下文注入
  - diary 写入、修订和 revision
  - relationship 写入、修订和审计
  - self-care 对 episodic memory 的归档和 summary
  - context build 候选、分数、入选、排除原因审计
  - memory 相关事件进入 replay/trace 体系

  一句话判断

  你的代码目前实现了 memory 系统的“可复现内核”：私有记忆、日记、关系、身份锚点、确定性摘要和上下文选择审计。文档设计中更高级的部分还缺失，尤其是 Neural
  Link 共享、LLM 认知摘要、conversation 归档摘要、token budget 管理、diary 搜索和完整 consent/privacy 流程。
