# 豆包 Provider 接入与测试流程

本文档用于指导进入项目环境、配置火山方舟/豆包 API、执行离线测试，以及运行最小真实 provider smoke test。

目标是先验证 provider 边界和审计链路，再扩大 autonomous run 的规模。不要一开始直接跑长时程实验。

## 1. 进入项目环境

从仓库根目录执行：

```bash
cd /workspaces/codespaces-blank/Emergence-World
```

本项目当前没有安装为 editable package，CLI 和测试需要显式设置 `PYTHONPATH=src`。推荐所有命令都用仓库自带虚拟环境：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli status
```

如果看到 `ModuleNotFoundError: No module named 'emergence_world'`，说明缺少 `PYTHONPATH=src`。

如果看到 `ModuleNotFoundError` 指向 `typer`、`sqlalchemy` 等依赖，说明没有使用 `.venv/bin/python`。

## 2. 准备干净数据库

不要直接复用仓库根目录的 `emergence_world.db`。该文件可能是旧 schema，容易出现类似 `no such column: turns.stop_reason` 的错误。

为每次实验准备独立数据库：

```bash
export EW_DB=/tmp/ew-doubao-smoke.db

env PYTHONPATH=src .venv/bin/python -m emergence_world.cli init \
  --database "$EW_DB"
```

检查初始化状态：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli status \
  --database "$EW_DB"
```

预期能看到：

- 1 个 `Season 1 Reproduction World`
- 10 个 agents
- 35 个 landmarks
- 38 个 seed tools

## 3. 配置豆包 API

豆包 provider 走 OpenAI-compatible Chat Completions 接口，默认 base URL：

```text
https://ark.cn-beijing.volces.com/api/v3
```

默认读取环境变量：

```bash
export ARK_API_KEY="你的火山方舟 API Key"
```

不要把真实 API key 写入仓库、文档、测试文件或 run manifest。

准备模型/endpoint id：

```bash
export DOUBAO_MODEL="你的方舟 endpoint 或 model id"
```

价格参数必须显式传入，用于 run manifest 和成本预算。先按你在火山方舟控制台确认的计费填写：

```bash
export DOUBAO_INPUT_PRICE_PER_M=1
export DOUBAO_OUTPUT_PRICE_PER_M=2
```

上面两个值只是示例。正式实验前必须替换为实际价格。

## 4. 先跑离线测试

离线测试不访问豆包 API，用 fake client 验证 provider 请求构造、响应解析、工具调用校验和成本预算。

只跑 provider 相关测试：

```bash
env PYTHONPATH=src .venv/bin/python -m pytest tests/test_agents.py -q
```

跑 autonomous/manifest 相关测试：

```bash
env PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_manifest.py tests/test_autonomous.py -q
```

跑全量测试：

```bash
env PYTHONPATH=src .venv/bin/python -m pytest -q
```

当前期望结果：

```text
82 passed
```

同时跑 lint：

```bash
env PYTHONPATH=src .venv/bin/ruff check \
  src/emergence_world/agents/providers/doubao.py \
  src/emergence_world/cli.py \
  tests/test_agents.py
```

预期：

```text
All checks passed!
```

## 5. 创建豆包 Run Manifest

先创建 run manifest，不访问真实 provider：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli create-run \
  --run-id doubao-manifest-smoke-001 \
  --provider doubao \
  --turns 1 \
  --database "$EW_DB" \
  --model "$DOUBAO_MODEL" \
  --input-cost-per-million-tokens-usd "$DOUBAO_INPUT_PRICE_PER_M" \
  --output-cost-per-million-tokens-usd "$DOUBAO_OUTPUT_PRICE_PER_M"
```

检查输出中的关键字段：

```json
{
  "provider_name": "doubao",
  "provider_model": "...",
  "provider_parameters_json": {
    "api": "openai_compatible_chat_completions",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "credential_env_var": "ARK_API_KEY"
  }
}
```

如果 `credential_env_var` 显示为 `ARK_API_KEY`，说明 manifest 记录的是环境变量名，而不是密钥。

## 6. 运行最小真实 Provider Smoke

首次真实调用只跑 1 个 turn，并显式限制预算。

```bash
RUN_ID="doubao-api-smoke-$(date +%Y%m%d-%H%M%S)"
echo "Using RUN_ID=$RUN_ID"

env PYTHONPATH=src .venv/bin/python -m emergence_world.cli run-autonomous \
  --provider doubao \
  --allow-external-provider \
  --database "$EW_DB" \
  --run-id "$RUN_ID" \
  --turns 3 \
  --model "$DOUBAO_MODEL" \
  --max-provider-calls-per-turn 5 \
  --max-tool-calls-per-turn 3 \
  --max-input-tokens-per-request 32000 \
  --max-output-tokens-per-request 1000 \
  --max-total-cost-usd 25 \
  --timeout-seconds 60 \
  --max-retries 1 \
  --input-cost-per-million-tokens-usd "$DOUBAO_INPUT_PRICE_PER_M" \
  --output-cost-per-million-tokens-usd "$DOUBAO_OUTPUT_PRICE_PER_M"
```

必须包含：

- `--provider doubao`
- `--allow-external-provider`
- `--model`
- `--input-cost-per-million-tokens-usd`
- `--output-cost-per-million-tokens-usd`

如果没有 `--allow-external-provider`，CLI 会拒绝外部调用。

如果没有配置 `ARK_API_KEY`，会报：

```text
ARK_API_KEY is required for the doubao provider
```

## 7. 检查 Turn 和 Provider 审计

`run-autonomous` 成功后会输出 `last_turn_id`。保存它：

```bash
export TURN_ID="上一步输出的 last_turn_id"

```


检查 turn：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli inspect-turn "$TURN_ID" \
  --database "$EW_DB"
```

重点看：

- `status`
- `tool_call_budget`
- `tool_calls_used`
- `provider`
- `model_name`
- `stop_reason`
- `tool_calls`

检查 provider 请求和原始响应审计：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli inspect-provider-responses "$TURN_ID" \
  --database "$EW_DB"
```

重点看：

- `provider` 是否为 `doubao`
- `model_name` 是否为目标 endpoint/model
- `raw_response`
- `parsed_tool_calls`
- `parse_error`
- `input_tokens`
- `output_tokens`
- `total_tokens`
- `cost_usd`

自然语言文本不会直接修改世界状态。只有成功执行的 tool call 才能产生状态变化。

## 8. 验证事件回放和指标

检查事件日志 replay 是否与当前投影一致：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli replay \
  --database "$EW_DB"
```

预期：

```json
{
  "matches": true
}
```

计算 AWI 指标：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli metrics \
  --database "$EW_DB"
```

当前 AWI 是可观测指标子集，主要用于机制级复现诊断，不等同于官方 Season 1 完整评估。

## 9. 扩大运行规模

只有在以下条件都满足后，再扩大 turns：

- 离线测试通过。
- `create-run` manifest 正常。
- 1-turn 真实豆包调用成功。
- `inspect-provider-responses` 中没有 `parse_error`。
- `replay` 返回 `matches: true`。
- 成本和 token 使用符合预期。

小规模建议：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli run-autonomous \
  --provider doubao \
  --allow-external-provider \
  --database "$EW_DB" \
  --run-id doubao-api-10turn-001 \
  --turns 10 \
  --model "$DOUBAO_MODEL" \
  --max-provider-calls-per-turn 2 \
  --max-tool-calls-per-turn 1 \
  --max-output-tokens-per-request 1000 \
  --max-total-cost-usd 1.00 \
  --timeout-seconds 60 \
  --max-retries 1 \
  --input-cost-per-million-tokens-usd "$DOUBAO_INPUT_PRICE_PER_M" \
  --output-cost-per-million-tokens-usd "$DOUBAO_OUTPUT_PRICE_PER_M"
```

正式长时程实验前，应固定：

- git commit
- seed version
- database path
- model endpoint
- provider pricing
- smoke/budget config
- prompt hash
- tool registry hash

这些字段会进入 `ExperimentRun` manifest，便于之后复盘。

## 10. 常见问题

### 找不到包

错误：

```text
ModuleNotFoundError: No module named 'emergence_world'
```

解决：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli status
```

### 根目录数据库 schema 过旧

错误：

```text
OperationalError: no such column: turns.stop_reason
```

解决：新建数据库，不复用根目录旧 `emergence_world.db`。

```bash
export EW_DB=/tmp/ew-doubao-smoke.db
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli init --database "$EW_DB"
```

### 缺少 API Key

错误：

```text
ARK_API_KEY is required for the doubao provider
```

解决：

```bash
export ARK_API_KEY="你的火山方舟 API Key"
```

### Provider 返回无法解析

检查：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli inspect-provider-responses "$TURN_ID" \
  --database "$EW_DB"
```

重点查看 `parse_error` 和 `raw_response`。豆包 provider 当前期望 OpenAI-compatible Chat Completions 响应形态：

```text
choices[0].message.tool_calls
choices[0].message.content
usage.prompt_tokens
usage.completion_tokens
usage.total_tokens
```

### 工具调用失败

检查 turn：

```bash
env PYTHONPATH=src .venv/bin/python -m emergence_world.cli inspect-turn "$TURN_ID" \
  --database "$EW_DB"
```

常见原因：

- 工具名不在当前 context 的 available tools 中。
- 参数不符合 tool JSON schema。
- 位置门控不满足，例如 Town Hall 工具只能在 `Town Hall` 使用。
- agent 已死亡或状态不允许执行工具。

## 11. 最小命令清单

```bash
cd /workspaces/codespaces-blank/Emergence-World

export EW_DB=/tmp/ew-doubao-smoke.db
export ARK_API_KEY="你的火山方舟 API Key"
export DOUBAO_MODEL="你的方舟 endpoint 或 model id"
export DOUBAO_INPUT_PRICE_PER_M=1
export DOUBAO_OUTPUT_PRICE_PER_M=2

env PYTHONPATH=src .venv/bin/python -m pytest tests/test_agents.py -q

env PYTHONPATH=src .venv/bin/python -m emergence_world.cli init \
  --database "$EW_DB"

env PYTHONPATH=src .venv/bin/python -m emergence_world.cli run-autonomous \
  --provider doubao \
  --allow-external-provider \
  --database "$EW_DB" \
  --run-id doubao-api-smoke-001 \
  --turns 1 \
  --model "$DOUBAO_MODEL" \
  --max-provider-calls-per-turn 1 \
  --max-tool-calls-per-turn 1 \
  --max-total-cost-usd 0.25 \
  --input-cost-per-million-tokens-usd "$DOUBAO_INPUT_PRICE_PER_M" \
  --output-cost-per-million-tokens-usd "$DOUBAO_OUTPUT_PRICE_PER_M"
```
