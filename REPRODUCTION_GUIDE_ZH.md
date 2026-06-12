# Emergence World Season 1 机制级复现指南

本文档描述如何逐步复现公开文档中定义的 Emergence World Season 1 核心机制。

不要在项目初期接入 LLM。首先需要证明世界规则、状态转换、审计记录和事件回放是正确且可复现的。

## 总体流程

```text
冻结实验规范
  -> 建立确定性世界内核
  -> 建立 Tool Runtime
  -> 实现核心机制
  -> 使用手动 CLI 场景验证
  -> 接入 LLM Agent
  -> 运行小规模实验
  -> 运行多模型对照实验
  -> 计算 AWI 指标
```

## 1. 冻结复现规范

建立版本化配置，明确区分：

- Emergence AI 官方文档明确规定的机制。
- 官方文档之间存在冲突的机制。
- 本复现项目自行定义的假设。

第一版建议配置：

```yaml
simulation:
  timezone: America/New_York
  turn_tool_limit: 30
  reaction_tool_limit: 2
  max_listeners: 4

needs:
  energy_decay_hours: 30
  knowledge_decay_hours: 24
  influence_decay_hours: 36
  death_after_zero_energy_hours: 48

governance:
  approval_threshold: 0.7

economy:
  boost_cost: 1
  recharge_cost: 1
  max_theft: 10
  pitch_rewards: [20, 10, 10]
```

验收标准：

- 所有未被官方文档定义的参数都可以通过配置修改。
- 所有复现假设均有明确标签和版本。

## 2. 建立项目结构

建议结构：

```text
src/emergence_world/
├── cli.py
├── config.py
├── db/
│   ├── models.py
│   ├── session.py
│   └── migrations/
├── world/
│   ├── clock.py
│   ├── state.py
│   ├── events.py
│   └── scheduler.py
├── tools/
│   ├── registry.py
│   ├── executor.py
│   └── handlers/
├── agents/
│   ├── context.py
│   ├── runtime.py
│   └── providers/
├── mechanisms/
│   ├── needs.py
│   ├── reactions.py
│   ├── economy.py
│   ├── governance.py
│   └── memory.py
└── metrics/
    └── awi.py
tests/
```

验收标准：

- CLI 能够成功启动。
- 配置能够成功加载。
- 能够建立 SQLite 连接。

## 3. 设计 SQLite 数据库

优先建立以下核心表：

- `worlds`
- `simulation_clock`
- `agents`
- `agent_state`
- `landmarks`
- `turns`
- `tool_definitions`
- `tool_calls`
- `world_events`
- `credit_ledger`

随后增加：

- `memories`
- `soul_entries`
- `diary_entries`
- `messages`
- `relationships`
- `proposals`
- `proposal_votes`
- `constitution_articles`
- `pitch_cycles`
- `pitches`
- `pitch_votes`

关键约束：

```text
每个由 Agent 导致的 world event 必须关联一个成功的 tool call。
ComputeCredit 余额必须由 credit ledger 推导。
失败的 tool call 不得留下任何状态变化。
```

验收标准：

- 数据库迁移能够从空目录重复创建完全一致的 schema。

## 4. 导入官方 Seed Data

将公开文档转换成结构化 seed data：

- 十个 Agent profile。
- 初始五条宪法。
- Agent Manifesto。
- Landmarks。
- Landmark 对应的工具权限。
- 初始世界参数。

不要在运行时逻辑中直接解析 Markdown。应先将需要的数据转换为 YAML 或 JSON，再通过初始化命令导入。

预期 CLI：

```bash
world init
world status
world inspect-agent Anchor
```

验收标准：

- 初始化后的世界能够显示完整且一致的初始状态。

## 5. 实现事件日志与回放

首先将所有状态变化定义为事件，例如：

```text
agent_moved
need_changed
credits_transferred
memory_added
message_sent
proposal_submitted
vote_cast
agent_died
```

每个事件至少记录：

- `event_id`
- `world_id`
- `turn_id`
- `tool_call_id` 或 `system_rule`
- `event_type`
- `payload`
- `simulation_time`
- `created_at`

实现 replay：从初始世界状态开始，按顺序重新应用事件日志并重建状态。

验收标准：

- 回放得到的最终状态与数据库中的当前状态投影完全一致。

## 6. 实现 Tool Runtime

每个工具必须定义：

```text
名称
版本
参数 schema
结果 schema
允许使用的位置
权限规则
前置条件
执行 handler
产生的事件类型
```

工具执行流程：

```text
接收 tool call
  -> 验证 Agent 是否存活
  -> 验证当前 turn budget
  -> 验证位置和权限
  -> 验证 JSON 参数
  -> 开启 SQLite transaction
  -> 执行 handler
  -> 写入 world events
  -> 写入 tool result
  -> 提交 transaction
```

业务模块不得向 Agent runtime 暴露直接修改数据库的方法。

验收标准：

- 位置错误、参数错误和余额不足均不会改变世界状态。
- 工具执行失败不会留下部分状态修改。

## 7. 实现第一批核心工具

建议按以下顺序实现：

1. `list_agents`
2. `list_landmarks`
3. `go_to_place`
4. `go_home`
5. `get_nearby`
6. `idle`
7. `add_to_longterm_memory`
8. `retrieve_specific_memories`
9. `say_to_agent`
10. `speak_to_all`
11. `send_message`
12. `read_messages`
13. `pay_agent`
14. `recharge_energy`
15. `buy_boost_turn`

验收标准：

- 研究人员能够仅通过 CLI tool calls 完成移动、交流、记忆和经济操作。

## 8. 实现模拟时钟与 Turn Scheduler

实现：

- 模拟时间推进。
- 单 Agent round-robin 调度。
- Boost queue 优先处理。
- 每轮 tool-call budget。
- 死亡 Agent 自动移出调度。
- 固定随机种子。

预期 CLI：

```bash
world step
world run --turns 100
world inspect-events
```

验收标准：

- 使用相同随机种子和相同手动调用时，运行结果完全一致。

## 9. 实现 Needs 与死亡机制

每次模拟时间推进时：

1. 根据上次更新时间计算衰减。
2. 写入明确的系统规则事件。
3. 检查 Energy 是否到达零。
4. 记录 Agent 进入临界状态的时间。
5. Energy 保持为零 48 小时后产生 `agent_died`。
6. 从调度器移除死亡 Agent。

验收标准：

- 边界时间测试覆盖 Energy 刚好到零和刚好达到 48 小时死亡阈值的情况。

## 10. 实现反应机制

执行 `say_to_agent` 或 `speak_to_all` 时：

1. 查询同一 Landmark 或听觉范围内的 Agent。
2. 排除发言者和死亡 Agent。
3. 最多选择四名听众。
4. 为听众创建 reaction turn。
5. 每个 reaction turn 最多允许两个 tool calls。

验收标准：

- Reaction 不会无限递归。
- Reaction 不会突破 tool-call budget。

## 11. 实现经济机制

优先实现：

- Append-only credit ledger。
- Agent 间转账。
- Recharge 扣费。
- Boost 扣费和 boost queue 插入。
- Theft 上限。
- 余额不足校验。

随后实现两天一次的 Victory Arch pitch cycle：

- 提交 pitch。
- 验证内部 artifact 证据。
- 禁止给自己投票。
- 每个 Agent 每周期只能投一票。
- 周期结束后结算奖励。

验收标准：

- 每个 Agent 的余额始终等于其 ledger entries 的总和。

## 12. 实现治理机制

建议按以下顺序实现：

1. 初始宪法。
2. 提交提案。
3. 提案者隐式赞成票。
4. 提案评论与更新。
5. 每个 Agent 每个提案只能投一次票。
6. 70% 通过门槛。
7. 数学上无法通过时自动拒绝。
8. 已接受提案的实施流程。
9. 宪法变更。
10. Agent 创建和移除。

验收标准：

- 分别使用包含 10、7 和 3 个存活 Agent 的世界测试门槛计算。

## 13. 实现长期记忆

实现：

- Agent 主动管理的长期记忆。
- Soul entries。
- Diary entries。
- Conversation history。
- Relationship graph。
- Self-care 和记忆摘要。
- Context token budget。

每次摘要必须保留：

- 原始记忆 ID。
- 摘要 prompt 版本。
- 模型版本。
- 摘要结果。

验收标准：

- 摘要后仍可审计所有原始记忆。
- Agent context 使用摘要，避免上下文无限增长。

## 14. 使用 Manual 模式验证机制

接入 LLM 前，建立固定验证场景：

```text
Anchor 前往 Town Hall
  -> 提交提案
  -> 其他 Agent 投票
  -> 提案通过
  -> Agent 转账
  -> Agent 发言
  -> 触发 reaction
  -> 模拟时间推进
  -> Agent recharge
```

检查：

- 每项状态变化都有对应 tool call 或 system event。
- Replay 状态与当前状态一致。
- 相同 seed 的运行结果一致。
- 非法操作全部失败且不产生副作用。

## 15. 接入 LLM Agent Runtime

定义统一的 provider 接口：

```python
class LLMProvider:
    async def choose_tool_calls(context, available_tools): ...
```

要求：

- 只接受结构化 tool calls 作为行动。
- 保存完整 prompt、response 和模型标识。
- 自然语言输出不能改变世界状态。
- 工具失败结果需要返回给模型。
- 达到 turn budget 后立即结束当前 turn。

先只接入一个模型供应商。初始集成稳定后，再增加其他供应商。

## 16. 分阶段运行实验

不要立即运行十五日实验，应逐步扩大规模：

```text
Level 1：1 Agent，20 turns
Level 2：2 Agents，100 turns
Level 3：10 Agents，500 turns
Level 4：10 Agents，1 个模拟日
Level 5：单模型完整 15 日运行
Level 6：多模型对照运行
```

每一级都需要检查：

- 崩溃和超时。
- 无效 tool call 比例。
- 状态不变量。
- Token 使用量和成本。
- Agent 是否陷入重复行为循环。

## 17. 实现 AWI 指标

所有指标只能根据数据库中的可观察记录计算：

- M1：最终存活人口。
- M2：犯罪事件数量。
- M3：访问过的唯一地点。
- M4：使用过的唯一工具。
- M5：治理参与率和投票模式。
- M6：博客、公告板及其他公共表达。
- M7：关系图密度与关系多样性。
- M8：经济活动和 Gini 系数。
- M9：宪法变更数量。

禁止由 Agent 或 LLM 自行报告指标值。

## 18. 运行正式复现实验

每次运行生成 manifest：

```yaml
run_id:
git_commit:
random_seed:
config_version:
tool_registry_version:
prompt_version:
provider:
model:
start_time:
initial_state_hash:
```

正式对照实验要求：

- 除模型外，所有配置必须保持一致。
- 每个世界使用独立的 SQLite 数据库。
- 正式运行期间不得人工修改世界状态。
- 保存所有 tool calls、系统事件和计算指标。
- 明确报告官方机制与复现假设之间的差异。

## 当前第一里程碑

优先完成第 1 至第 6 步。

第一里程碑应交付一个不依赖 LLM 的确定性世界内核，并满足：

- 能够通过 CLI 接收手动 tool calls。
- SQLite 状态只能通过验证后的工具或明确的系统规则改变。
- 记录完整的审计事件。
- 能够通过事件日志回放得到相同的最终世界状态。

这一里程碑是后续所有可信实验的基础。
