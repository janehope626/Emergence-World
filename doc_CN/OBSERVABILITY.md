# 执行追踪操作

执行追踪会与世界事件分开持久化。command 是追踪根；span、provider 交互、工具调用、事件和状态差异都链接到该根或其 turn。

## 查询

先列出轻量摘要：

```bash
world list-traces --limit 50
world list-traces --stage provider --status completed
world list-traces --from 2026-06-21T00:00:00Z --to 2026-06-22T00:00:00Z
```

检查一个 command 或 turn：

```bash
world inspect-trace --latest
world inspect-trace --command COMMAND_ID --stage tool_handler
world inspect-trace --turn TURN_ID --offset 0 --limit 100
```

默认详情响应不包含记录下来的 span 输入/输出和原始 provider 请求/响应。只有确实需要这些可能很大的值时才使用 `--include-payloads`。Span 及相关集合通过 `--offset/--limit` 和 `--related-offset/--related-limit` 拥有独立的有界分页。

源码位置以仓库相对路径存储。旧版本创建的既有 trace 可能仍保留绝对路径。

## 保留策略

只有同时满足以下两个条件时，保留策略才会删除 trace：

1. 它早于 `--older-than-days`。
2. 它不属于所选范围中最新的 `--keep-latest` 条 trace。

除非提供 `--execute`，否则命令只执行 dry run：

```bash
world prune-traces --older-than-days 30 --keep-latest 100
world prune-traces --older-than-days 30 --keep-latest 100 --execute
```

删除一个 command 会级联删除其执行 span 和状态差异。Provider 审计、工具调用和世界事件记录仍保留为实验审计轨迹的一部分。

## REST 和 WebSocket 服务

启动单 worker 可观测性服务：

```bash
world serve --host 127.0.0.1 --port 8000
```

在开发 UI 或测试空数据库时生成安全的脚本化 trace：

```bash
world demo-trace
```

该命令会在必要时初始化空数据库，然后执行一个脚本化自主回合。它永远不会联系外部模型 provider，并且可以重复运行。

使用 `--cors-origins` 配置显式浏览器来源。除非设置 `EMERGENCE_TRACE_PAYLOAD_TOKEN`，否则完整记录的 span 和 provider payload 默认禁用；客户端必须在 `X-Trace-Payload-Token` 中发送相同值，并通过 `include_payloads=true` 显式选择加入。

`/api/v1` 下提供版本化只读资源：

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

实时轻量事件通过 `WS /ws/v1/traces` 发送。可选的 `world_id` 和 `command_id` 查询参数可过滤订阅。`after_sequence` 可从已提交的 outbox 游标恢复连接。服务进程中在世界事务提交前发出的事件带有 `provisional=true`；持久化 outbox 事件以 `provisional=false` 交付，`command.committed` 确认成功提交。`stream.gap` 提示慢客户端通过 REST 对账，或使用最后提交游标重连。

内存 broker 提供低延迟临时事件。`trace_stream_events` 数据库 outbox 提供已提交的跨进程交付，因此单独的 `world` CLI 进程在事务提交后对服务可见。默认命令仍运行一个 Uvicorn worker；对于高流量多实例部署，共享 Redis 或 PostgreSQL transport 是合适的升级路径。
