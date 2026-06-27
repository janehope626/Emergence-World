# Project Command

本文档记录本仓库当前可用的后端、前端、API、测试和验证命令。命令基于代码中的 Typer CLI、FastAPI 服务、Alembic 配置和 `frontend/package.json` 整理。

## 0. 基础环境

所有后端命令默认在仓库根目录执行：

```bash
cd /workspaces/codespaces-blank/Emergence-World
source .venv/bin/activate
```

如果 `world` 命令不存在，说明当前项目尚未以 editable 模式安装：

```bash
pip install -e .
```

检查 CLI 是否可用：

```bash
world --help
```

后端默认数据库是 `emergence_world.db`。多数命令都支持：

```bash
--database path/to/world.db
```

当一个数据库中存在多个 world 时，相关命令可用：

```bash
--world "Season 1 Reproduction"
```

## 1. 推荐快速启动流程

初始化数据库并导入 Season 1 机制复现种子：

使用 emergence_world.db 这个数据库；
1.如果 schema 旧了，就升级；
2.如果没有seed对应的world，创建新world，写入SQLite数据库中的SQL表记录
3.有seed对应的world，就复用
Todo：seed版本控制
不会自动清空或重建世界。

```bash
world init --database emergence_world.db
```


SQL：有新增的表需求，写进migration

查看世界状态：

```bash
world status
```

生成一条安全的 scripted trace，用于前端和 API 调试。该命令不会访问外部模型：

```bash
world demo-trace
```

启动后端 REST + WebSocket 观测服务：

```bash
world serve --host 127.0.0.1 --port 8000 --cors-origins http://127.0.0.1:5173
```

启动前端：

```bash
cd frontend
npm install
npm run dev
```

浏览器访问：

```text
http://127.0.0.1:5173
```

Vite 开发服务器会把 `/api` 和 `/ws` 代理到 `127.0.0.1:8000`。

## 2. 后端数据库与迁移

初始化或升级数据库，并导入固定种子：

```bash
world init
world init --database test-world.db
world init --database test-world.db --random-seed 42
```

直接运行 Alembic 升级：

```bash
alembic upgrade head
```

检查 Alembic 迁移是否与 SQLAlchemy 模型一致：

```bash
alembic check
```

指定数据库 URL 执行 Alembic：

```bash
DATABASE_URL=sqlite:///./another-world.db alembic upgrade head
```

## 3. 世界查看命令

查看数据库里的 world、agent、landmark、tool 数量：

```bash
world status
world status --database emergence_world.db
```

查看单个 Agent 的 profile 和当前状态：

```bash
world inspect-agent Anchor
world inspect-agent Flora --database emergence_world.db
```

查看地标和该地标开放的 gated tools：

```bash
world inspect-landmark "Town Hall"
world inspect-landmark "Public Library"
```

列出所有 active tools：

```bash
world list-tools
```

查看单个工具的 schema、可用地点和事件类型：

```bash
world inspect-tool go_to_place
world inspect-tool add_to_memory
```

## 4. 手动工具调用

手动执行一个经过校验和审计的 tool call：

```bash
world call-tool Anchor go_to_place --arguments '{"place":"Central Plaza"}'
```

说明：

- `agent` 是 Agent 名称。
- `tool` 是工具名。
- `--arguments` 必须是 JSON object。
- 成功和失败都会输出 JSON。
- 该路径会经过工具可用性、参数校验、handler 执行和审计记录。

## 5. 确定性模拟

执行一个 deterministic turn，不调用 LLM：

```bash
world step
world step --minutes 30
```

批量执行 deterministic turns：

```bash
world run --turns 10
world run --turns 100 --minutes 30
```

用途：

- 快速推进世界机制。
- 验证 scheduler、needs、clock、event log 和 replay。
- 不产生真实 provider 调用成本。

## 6. 自主 Agent 运行

使用 scripted provider 运行自主 turn。该 provider 是本地固定脚本，不访问外部模型：

```bash
world run-autonomous --turns 1 --provider scripted
world run-autonomous --turns 10 --provider scripted --minutes 30
```

指定 run id：

```bash
world run-autonomous --turns 5 --provider scripted --run-id scripted-smoke-001
```

创建实验 run manifest，但不启动运行：

```bash
world create-run --run-id scripted-manifest-001 --provider scripted --turns 10
```

查看已保存的实验 run manifest：

```bash
world inspect-run scripted-manifest-001
```

正式接入真实 provider 前，先运行 readiness gate：

```bash
world readiness-check
world readiness-check --skip-tests
```

## 7. OpenAI Provider

OpenAI provider 使用 OpenAI Python SDK 的 Responses API。SDK 默认读取 `OPENAI_API_KEY`。

设置密钥：

```bash
export OPENAI_API_KEY=...
```

创建 OpenAI run manifest：

```bash
world create-run \
  --run-id openai-smoke-001 \
  --provider openai \
  --turns 1 \
  --model gpt-5-mini \
  --input-cost-per-million-tokens-usd 0.25 \
  --output-cost-per-million-tokens-usd 2.00
```

运行 OpenAI provider。必须显式加 `--allow-external-provider`，避免误触发真实网络和费用：

```bash
world run-autonomous \
  --turns 1 \
  --provider openai \
  --allow-external-provider \
  --model gpt-5-mini \
  --input-cost-per-million-tokens-usd 0.25 \
  --output-cost-per-million-tokens-usd 2.00 \
  --max-total-cost-usd 0.25 \
  --max-provider-calls-per-turn 2 \
  --max-tool-calls-per-turn 1 \
  --max-output-tokens-per-request 1000 \
  --timeout-seconds 60 \
  --max-retries 1
```

常用安全参数：

- `--max-total-cost-usd`：整次 run 的成本上限。
- `--max-provider-calls-per-turn`：每 turn 最多 provider 调用次数。
- `--max-tool-calls-per-turn`：每 turn 最多工具调用次数。
- `--max-input-tokens-per-request`：单次请求输入 token 上限。
- `--max-output-tokens-per-request`：单次请求输出 token 上限。
- `--timeout-seconds`：provider 请求超时。
- `--max-retries`：provider SDK 重试次数。

## 8. Doubao / Volcengine Ark Provider

Doubao provider 使用 OpenAI-compatible chat completions 接口。默认 base URL：

```text
https://ark.cn-beijing.volces.com/api/v3
```

默认读取环境变量：

```bash
export ARK_API_KEY=...
```

创建 Doubao run manifest：

```bash
world create-run \
  --run-id doubao-smoke-001 \
  --provider doubao \
  --turns 1 \
  --model your-ark-model-id \
  --input-cost-per-million-tokens-usd 0.10 \
  --output-cost-per-million-tokens-usd 0.30
```

运行 Doubao provider：

```bash
world run-autonomous \
  --turns 1 \
  --provider doubao \
  --allow-external-provider \
  --model your-ark-model-id \
  --input-cost-per-million-tokens-usd 0.10 \
  --output-cost-per-million-tokens-usd 0.30 \
  --max-total-cost-usd 0.25
```

## 9. Turn、Context、Provider 审计查看

查看一个 turn 及其 tool call 结果：

```bash
world inspect-turn TURN_ID
```

查看某个 turn 开始时提供给 Agent 的 immutable context：

```bash
world inspect-context TURN_ID
```

查看 provider 请求、原始响应、解析后的 tool calls、token、延迟和成本：

```bash
world inspect-provider-responses TURN_ID
```

注意：这些命令可能输出较大的 JSON。provider payload 已经过项目中的敏感字段脱敏逻辑处理，但仍应避免公开分享真实运行日志。

## 10. Trace 查询与清理

列出轻量 trace summary，不加载大 payload：

```bash
world list-traces
world list-traces --limit 50
world list-traces --stage provider
world list-traces --status completed
world list-traces --from 2026-06-21T00:00:00Z --to 2026-06-22T00:00:00Z
```

查看最新 trace：

```bash
world inspect-trace --latest
```

按 command id 查看 trace：

```bash
world inspect-trace --command COMMAND_ID
```

按 turn id 查看 trace：

```bash
world inspect-trace --turn TURN_ID
```

过滤 span：

```bash
world inspect-trace --latest --stage tool_handler
world inspect-trace --latest --status completed
```

分页：

```bash
world inspect-trace --latest --offset 0 --limit 100 --related-offset 0 --related-limit 100
```

显式包含 span input/output 和 provider request/response：

```bash
world inspect-trace --latest --include-payloads
```

清理旧 trace。默认 dry-run，不会删除：

```bash
world prune-traces --older-than-days 30 --keep-latest 100
```

真正执行删除：

```bash
world prune-traces --older-than-days 30 --keep-latest 100 --execute
```

清理规则：

- 只删除同时满足“超过指定天数”和“不属于最新 N 条”的 command trace。
- 删除 command trace 会级联删除 execution spans 和 state diffs。
- Provider audit、tool calls 和 world events 仍保留在实验审计记录中。

## 11. Replay 与 AWI Metrics

回放 event log 并验证当前 projection 是否可重放：

```bash
world replay
```

如果 `matches=false`，说明当前 projection 与 event log replay 不一致，应先停止继续实验并排查。

计算当前可观察的 AWI 指标和诊断数据：

```bash
world metrics
```

## 12. REST API 服务

启动单 worker FastAPI REST + WebSocket 服务：

```bash
world serve --host 127.0.0.1 --port 8000
```

允许本地 Vite 前端跨域访问：

```bash
world serve --host 127.0.0.1 --port 8000 --cors-origins http://127.0.0.1:5173
```

开启完整 payload 读取授权：

```bash
export EMERGENCE_TRACE_PAYLOAD_TOKEN=change-me
world serve --host 127.0.0.1 --port 8000 --cors-origins http://127.0.0.1:5173
```

REST 端点：

```text
GET /api/v1/health
GET /api/v1/traces
GET /api/v1/traces/{command_id}
GET /api/v1/traces/{command_id}/spans
GET /api/v1/traces/{command_id}/provider-interactions
GET /api/v1/traces/{command_id}/tool-calls
GET /api/v1/traces/{command_id}/events
GET /api/v1/traces/{command_id}/state-diffs
```

WebSocket 端点：

```text
WS /ws/v1/traces
```

WebSocket 可选 query 参数：

```text
world_id=...
command_id=...
after_sequence=...
```

使用 curl 检查健康状态：

```bash
curl http://127.0.0.1:8000/api/v1/health
```

列出 traces：

```bash
curl "http://127.0.0.1:8000/api/v1/traces?limit=20"
```

读取 span payload 时需要 header：

```bash
curl \
  -H "X-Trace-Payload-Token: change-me" \
  "http://127.0.0.1:8000/api/v1/traces/COMMAND_ID/spans?include_payloads=true"
```

## 13. 前端开发

进入前端目录：

```bash
cd frontend
```

安装依赖：

```bash
npm install
```

启动开发服务器：

```bash
npm run dev
```

默认地址：

```text
http://127.0.0.1:5173
```

前端开发代理：

- `/api` -> `http://127.0.0.1:8000`
- `/ws` -> `ws://127.0.0.1:8000`

覆盖 API 和 WebSocket 地址：

```bash
VITE_API_BASE=http://127.0.0.1:8000/api/v1 npm run dev
VITE_WS_URL=ws://127.0.0.1:8000/ws/v1/traces npm run dev
```

生产构建：

```bash
npm run build
```

本地预览生产构建：

```bash
npx vite preview
```

前端功能：

- Trace 列表。
- world、stage、status 过滤。
- Command 详情。
- Span 父子调用图。
- Provider interactions。
- Tool calls。
- World events。
- State diffs。
- JSON 只读查看。
- WebSocket 实时事件和 REST 对账。

## 14. 前端测试与验证

运行 Vitest 单元测试：

```bash
cd frontend
npm run test
```

运行 ESLint：

```bash
npm run lint
```

运行 TypeScript + Vite production build：

```bash
npm run build
```

安装 Playwright Chromium：

```bash
npx playwright install chromium
```

运行 Playwright E2E：

```bash
npm run test:e2e
```

## 15. 后端测试与质量检查

运行 Python 测试：

```bash
pytest
```

运行指定测试文件：

```bash
pytest tests/test_api.py
pytest tests/test_observability.py
```

运行 Ruff：

```bash
ruff check .
```

运行 MyPy：

```bash
mypy src tests
```

检查 diff 是否有尾随空格等问题：

```bash
git diff --check
```

推荐完整后端验证：

```bash
pytest
ruff check .
mypy src tests
alembic check
world replay
git diff --check
```

推荐完整前端验证：

```bash
cd frontend
npm run test
npm run lint
npm run build
npm run test:e2e
```

## 16. 常见开发工作流

只验证后端机制：

```bash
source .venv/bin/activate
world init
world run --turns 10
world replay
world metrics
```

验证自主 Agent trace：

```bash
source .venv/bin/activate
world demo-trace
world list-traces --limit 5
world inspect-trace --latest
```

联调前后端：

```bash
# terminal 1
source .venv/bin/activate
world demo-trace
world serve --host 127.0.0.1 --port 8000 --cors-origins http://127.0.0.1:5173

# terminal 2
cd frontend
npm run dev
```

准备真实 provider smoke run：

```bash
source .venv/bin/activate
world readiness-check
world run-autonomous --turns 1 --provider scripted
world replay
```

真实 provider 最小运行前置要求：

- `world readiness-check` 结果可接受。
- 数据库已备份。
- 明确设置 provider API key。
- 明确设置 token 价格。
- 使用 `--allow-external-provider`。
- 设置低成本、低 turn 数、低 tool call 上限。

## 17. 文件与目录说明

后端主要入口：

```text
src/emergence_world/cli.py
src/emergence_world/api/app.py
src/emergence_world/api/routes/traces.py
src/emergence_world/world/runtime.py
src/emergence_world/agents/runtime.py
src/emergence_world/tools/executor.py
src/emergence_world/observability/tracer.py
src/emergence_world/observability/query.py
src/emergence_world/observability/stream.py
```

前端主要入口：

```text
frontend/src/App.tsx
frontend/src/api.ts
frontend/src/useTraceStream.ts
frontend/src/types.ts
frontend/src/styles.css
frontend/vite.config.ts
```

测试入口：

```text
tests/
frontend/src/*.test.tsx
frontend/e2e/
```

## 18. 不建议直接执行的操作

不要直接修改 SQLite 表来改变世界状态。所有 Agent 造成的状态变化应通过 tool call 或世界机制产生。

不要在没有备份和成本限制的情况下运行真实 provider。

不要公开包含 provider request/response 的完整 trace payload。

不要把以下文件提交到 Git：

```text
*.db
*.db-shm
*.db-wal
frontend/node_modules/
frontend/dist/
frontend/test-results/
frontend/playwright-report/
frontend/*.tsbuildinfo
```

