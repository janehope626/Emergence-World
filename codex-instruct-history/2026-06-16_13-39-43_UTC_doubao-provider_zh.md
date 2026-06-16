# 2026-06-16 本轮对话记录：理解 Emergence World 并接入豆包 Provider

## 环境

- 工作目录：`/workspaces/codespaces-blank/Emergence-World`
- Shell：`bash`
- 日期：`2026-06-16`
- 时区：`Etc/UTC`
- 仓库：Emergence World 机制级复现项目

## 用户请求 1

> 我在复现这个实验，https://www.emergence.ai/blog/emergence-world-a-laboratory-for-evaluating-long-horizon-agent-autonomy，阅读并理解git项目

## 助手执行过程 1

助手先阅读了用户给出的 Emergence AI 博客页面，提取了实验目标和架构要点：

- Emergence World 不是短任务 benchmark，而是长时程、多智能体、持续运行的社会模拟平台。
- 实验关注行为漂移、治理、资源压力、关系网络、工具发现、跨模型影响和长期稳定性。
- 官方博客描述的线上平台包含：
  - React 18 + React Three Fiber/Three.js 前端。
  - Python 3.11+ FastAPI 后端。
  - PostgreSQL 持久化。
  - 多模型 agent：Claude、Gemini、Grok、GPT、混合模型世界。
  - 40+ 地点、120+ 工具、NYC 天气、新闻/API/互联网接入。
  - 三类持久记忆：episodic memory、reflective diaries、relationship state。
  - 民主治理、70% 通过阈值、经济压力和能量衰减。

随后助手检查本地仓库。最初尝试使用 `rg`，但环境中没有安装：

```text
/bin/bash: line 1: rg: command not found
```

于是改用 `find`、`sed`、`ls` 和 `git status` 查看项目结构。发现当前仓库没有未提交改动。

关键文件和目录：

- `README.md`
- `REPRODUCTION_GUIDE.md`
- `REPRODUCTION_GUIDE_ZH.md`
- `docs/ARCHITECTURE.md`
- `docs/ORCHESTRATION.md`
- `docs/tools/README.md`
- `src/emergence_world/cli.py`
- `src/emergence_world/db/models.py`
- `src/emergence_world/world/runtime.py`
- `src/emergence_world/tools/executor.py`
- `src/emergence_world/tools/registry.py`
- `src/emergence_world/seed/data/season_1_reproduction_v1.yaml`
- `tests/`

助手确认：本地仓库不是官方线上完整系统，而是一个“机制级复现”实现。它用 SQLite/SQLAlchemy、CLI、确定性世界内核和 provider 抽象复现公开机制，然后逐步接入 LLM。

## 项目理解总结

本项目当前目标：

- 先证明世界规则、状态转换、事件日志、审计记录和回放可复现。
- 再接入 LLM provider。
- 再做小规模实验和多模型对照。

核心模块：

- CLI：
  - `init`
  - `status`
  - `inspect-agent`
  - `inspect-landmark`
  - `list-tools`
  - `inspect-tool`
  - `call-tool`
  - `step`
  - `run`
  - `create-run`
  - `inspect-run`
  - `run-autonomous`
  - `readiness-check`
  - `inspect-turn`
  - `inspect-context`
  - `inspect-provider-responses`
  - `replay`
  - `metrics`

- 数据库模型：
  - `World`
  - `Experiment`
  - `ExperimentRun`
  - `SimulationClock`
  - `Agent`
  - `AgentState`
  - `Landmark`
  - `Turn`
  - `ToolCall`
  - `WorldEvent`
  - `ProviderInteraction`
  - `BoostTurnRequest`
  - `ReactionRequest`
  - `Message`
  - `Proposal`
  - `ProposalVote`
  - `Pitch`
  - `CreditLedgerEntry`
  - `EpisodicMemory`
  - `DiaryEntry`
  - `Relationship`

- 工具执行：
  - 所有 agent 行为必须通过 tool。
  - 工具执行会校验 agent 存活状态、位置门控、JSON schema。
  - 工具 handler 在事务中执行。
  - 成功调用会写入 `ToolCall` 和 `WorldEvent`。
  - 失败调用不能留下部分状态变更。

- 世界运行：
  - `run`：无 LLM 的确定性批处理，只推进时间、需求衰减和 pitch 结算。
  - `run-autonomous`：组装 agent context，调用 provider，执行工具，审计 provider 交互。

- Seed 数据：
  - `season_1_reproduction_v1.yaml` 定义 10 个 agent、35 个 landmark、38 个 seed tool、5 条宪法。
  - 明确记录了复现假设：
    - 官方未公开初始时间。
    - 官方未公开初始位置。
    - 官方未公开初始需求值。
    - 官方未公开初始 ComputeCredit 余额。
    - home assignment 是确定性复现假设。
    - tool schema 部分是占位或复现定义。

## 验证过程 1

最初直接执行：

```bash
python -m emergence_world.cli status
```

失败：

```text
ModuleNotFoundError: No module named 'emergence_world'
```

原因：项目未安装到当前解释器路径。随后使用 `PYTHONPATH=src`，但系统 Python 缺依赖：

```text
ModuleNotFoundError: No module named 'typer'
```

仓库中存在 `.venv`，但未 editable install，所以最终采用：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli status
```

成功输出：

```text
World: Season 1 Reproduction World
Status: ready
Agents: 10
Landmarks: 35
Tools: 35
```

查看 `Anchor`：

```text
Name: Anchor
Location: Central Plaza
Status: active
Needs: E=100 K=100 I=100
ComputeCredits: 0
```

查看工具列表时发现当前根目录数据库中 active tool 为 35 个，而 seed 文件声明 38 个工具。

直接在根目录现有 `emergence_world.db` 上运行：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli run --turns 3
```

失败：

```text
OperationalError: no such column: turns.stop_reason
```

判断：仓库根目录现有 `emergence_world.db` 是旧 schema，与当前 ORM/migrations 不一致。

随后全量测试：

```bash
env PYTHONPATH=src .venv/bin/python -m pytest -q
```

结果：

```text
79 passed in 47.39s
```

新建临时数据库：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli init --database /tmp/ew-fresh.db
```

输出：

```text
World created: ... (10 agents, 35 landmarks, 38 tools, 5 articles)
```

串行运行：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli run --turns 3 --database /tmp/ew-fresh.db
```

输出：

```json
{
  "turns": 3,
  "turns_requested": 3,
  "last_agent": "Blackbox",
  "simulation_time": "2026-01-01T01:30:00",
  "state_hash": "3d9f5865b9607898888492e8ae93bed3030db154e5be704820df8f49750c5dc9"
}
```

运行 scripted autonomous smoke：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli run-autonomous --turns 3 --provider scripted --database /tmp/ew-fresh.db --run-id smoke-scripted-001
```

输出：

```json
{
  "run_id": "smoke-scripted-001",
  "turns": 3,
  "turns_requested": 3,
  "last_agent": "Horizon",
  "last_stop_reason": "provider_done",
  "state_hash": "d5e85f54c5b5f1d73e7f296d81ff9548e188c5aba2ce9c4a653417330ef45b1f"
}
```

## 助手对用户的最终回复 1

助手总结：

- 已读完博客并梳理本地 Git 项目。
- 结论：仓库是 Emergence World 的机制级复现项目，不是官方完整线上系统。
- 本地实现重点复现公开机制：世界状态、agent、landmark、工具调用、事件日志、记忆、治理、经济、AWI 指标和可审计 autonomous turn。
- 建议后续复现实验时用新数据库，不要直接复用根目录旧 `emergence_world.db`。
- 当前只实现了 `scripted` 和 `openai` provider，尚未实现 Claude/Gemini/Grok provider。

建议路线：

1. 先用 `scripted` provider 跑小规模 autonomous smoke test。
2. 用 `replay` 验证事件日志能还原当前状态。
3. 用 `metrics` 计算 AWI。
4. 最后再接真实 LLM provider。

## 用户请求 2

> 准备先接入豆包模型，完善provider

## 助手执行过程 2

助手决定新增 `doubao` provider，不改写现有 `OpenAIProvider`。

原因：

- 现有 `OpenAIProvider` 使用 OpenAI Responses API。
- 豆包/火山方舟更适合走 OpenAI-compatible Chat Completions 形态：
  - `base_url`
  - `api_key`
  - `model`
  - `chat.completions.create`
  - `choices[0].message.tool_calls`

助手先搜索豆包/火山方舟 OpenAI-compatible 接入信息，但搜索结果没有稳定命中官方文档。于是按火山方舟常见 OpenAI-compatible 接口形态实现，保留：

- 显式 `base_url`
- 显式 model
- 显式成本参数
- 显式 `--allow-external-provider`
- 不硬编码价格
- 不自动外呼

阅读的现有 provider 相关文件：

- `src/emergence_world/agents/providers/base.py`
- `src/emergence_world/agents/providers/openai.py`
- `src/emergence_world/agents/providers/recording.py`
- `src/emergence_world/agents/providers/smoke.py`
- `src/emergence_world/agents/runtime.py`
- `src/emergence_world/agents/audit.py`
- `tests/test_agents.py`

## 代码改动 2

新增文件：

- `src/emergence_world/agents/providers/doubao.py`

主要内容：

- `DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"`
- `DoubaoProviderConfig`
  - `model`
  - `smoke_config`
  - `input_cost_per_million_tokens_usd`
  - `output_cost_per_million_tokens_usd`
  - `base_url`
  - `credential_env_var`
- `DoubaoProvider`
  - `provider_name = "doubao"`
  - 默认读取 `ARK_API_KEY`
  - 调用 `AsyncOpenAI(...).chat.completions.create(...)`
  - 使用 Chat Completions 工具格式：

```json
{
  "type": "function",
  "function": {
    "name": "...",
    "description": "...",
    "parameters": {}
  }
}
```

- 解析：

```text
choices[0].message.tool_calls[].function.name
choices[0].message.tool_calls[].function.arguments
choices[0].message.content
usage.prompt_tokens
usage.completion_tokens
usage.total_tokens
```

- 将豆包响应转换为项目内部 `AgentDecision`。
- 复用 `RecordingProvider` 做：
  - JSON schema 校验。
  - unknown tool 校验。
  - duplicate call id 校验。
  - tool-call budget 校验。
  - total cost budget 校验。

更新：

- `src/emergence_world/agents/providers/__init__.py`
  - export `DoubaoProvider`
  - export `DoubaoProviderConfig`

- `src/emergence_world/cli.py`
  - import `DoubaoProvider`, `DoubaoProviderConfig`
  - 新增 `doubao_provider_config(...)`
  - `create-run` 支持：

```text
--provider doubao
```

  - `run-autonomous` 支持：

```text
--provider doubao
--allow-external-provider
```

  - `provider must be scripted, openai, or doubao`
  - 对 `openai` 和 `doubao` 都要求显式 `--allow-external-provider`

- `tests/test_agents.py`
  - 新增 fake chat client：
    - `FakeChatCompletionsAPI`
    - `FakeChatAPI`
    - `FakeDoubaoClient`
  - 新增 `doubao_config(...)`
  - 新增测试：
    - `test_doubao_provider_builds_chat_request_and_parses_tool_call`
    - `test_doubao_provider_natural_language_cannot_change_state`
    - `test_doubao_provider_enforces_cost_budget_and_requires_api_key`

## 中途修正

第一次 CLI manifest smoke 输出中，字段 `api_key_env` 被通用 secret redactor 脱敏为 `[REDACTED]`。

原因：

- `security.py` 中 `SENSITIVE_KEY_MARKERS` 包含 `"api_key"`。
- redactor 会把包含该 marker 的 key 对应值替换成 `[REDACTED]`。

修正：

- 将配置字段改名为 `credential_env_var`。
- 保留 `validation_alias="api_key_env"`，兼容旧测试/配置传入。
- manifest 输出现在显示：

```json
"credential_env_var": "ARK_API_KEY"
```

## 验证过程 2

Targeted provider tests：

```bash
env PYTHONPATH=src .venv/bin/python -m pytest tests/test_agents.py -q
```

结果：

```text
22 passed in 2.01s
```

Manifest/autonomous 相关测试：

```bash
env PYTHONPATH=src .venv/bin/python -m pytest tests/test_manifest.py tests/test_autonomous.py -q
```

结果：

```text
19 passed in 17.34s
```

CLI manifest smoke：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli create-run \
  --run-id doubao-manifest-smoke-2 \
  --provider doubao \
  --turns 1 \
  --database /tmp/ew-fresh.db \
  --model doubao-test-endpoint \
  --input-cost-per-million-tokens-usd 1 \
  --output-cost-per-million-tokens-usd 2
```

输出中关键字段：

```json
{
  "provider_name": "doubao",
  "provider_model": "doubao-test-endpoint",
  "provider_parameters_json": {
    "api": "openai_compatible_chat_completions",
    "input_cost_per_million_tokens_usd": 1.0,
    "output_cost_per_million_tokens_usd": 2.0,
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "credential_env_var": "ARK_API_KEY"
  }
}
```

Ruff：

```bash
env PYTHONPATH=src .venv/bin/ruff check src/emergence_world/agents/providers/doubao.py src/emergence_world/cli.py tests/test_agents.py
```

结果：

```text
All checks passed!
```

全量测试：

```bash
env PYTHONPATH=src .venv/bin/python -m pytest -q
```

结果：

```text
82 passed in 46.78s
```

最终 `git status --short`：

```text
 M src/emergence_world/agents/providers/__init__.py
 M src/emergence_world/cli.py
 M tests/test_agents.py
?? src/emergence_world/agents/providers/doubao.py
```

## 助手对用户的最终回复 2

助手回复用户：

- 已接入豆包 provider。
- 新增 `doubao.py`，走 OpenAI-compatible Chat Completions 格式。
- 默认 `base_url` 为 `https://ark.cn-beijing.volces.com/api/v3`。
- 默认读取 `ARK_API_KEY`。
- `create-run` 和 `run-autonomous` 已支持 `--provider doubao`。
- 补了测试，覆盖：
  - 请求构造。
  - 工具调用解析。
  - 自然语言不产生状态变更。
  - 成本预算。
  - 缺少 API key。

给出的使用命令：

```bash
export ARK_API_KEY="你的火山方舟 API Key"

env PYTHONPATH=src .venv/bin/python -m emergence_world.cli run-autonomous \
  --provider doubao \
  --allow-external-provider \
  --database /tmp/ew-fresh.db \
  --turns 1 \
  --model "你的方舟 endpoint/model id" \
  --input-cost-per-million-tokens-usd 1 \
  --output-cost-per-million-tokens-usd 2
```

说明：

- 价格参数保留为显式必填，避免实验 manifest 中出现不清楚的成本假设。

## 用户请求 3

> 将本轮所有对话内容存到codex-instruct-history中

## 助手执行过程 3

助手检查现有目录：

```bash
ls -la codex-instruct-history
```

已有文件：

```text
2026-06-13_11-05-08_UTC_conversation.md
2026-06-14_12-07-29_UTC_memory-system_en.md
2026-06-14_12-07-29_UTC_memory-system_zh.md
2026-06-14_22-45-01_UTC_agent-loop.md
2026-06-15_22-09-01_UTC_manifest.md
```

获取 UTC 时间：

```bash
date -u +%Y-%m-%d_%H-%M-%S_UTC
```

结果：

```text
2026-06-16_13-39-43_UTC
```

本文件即为本轮对话记录。
