# Execution Trace Operations

Execution traces are persisted separately from world events. A command is the trace
root; spans, provider interactions, tool calls, events, and state diffs are linked to
that root or its turn.

## Querying

List lightweight summaries first:

```bash
world list-traces --limit 50
world list-traces --stage provider --status completed
world list-traces --from 2026-06-21T00:00:00Z --to 2026-06-22T00:00:00Z
```

Inspect one command or turn:

```bash
world inspect-trace --latest
world inspect-trace --command COMMAND_ID --stage tool_handler
world inspect-trace --turn TURN_ID --offset 0 --limit 100
```

The default detail response excludes recorded span inputs/outputs and raw provider
requests/responses. Use `--include-payloads` only when those potentially large values
are required. Span and related collections have independent bounded pagination via
`--offset/--limit` and `--related-offset/--related-limit`.

Source locations are stored as repository-relative paths. Existing traces created by
older revisions may retain absolute paths.

## Retention

The retention policy deletes a trace only when both conditions are true:

1. It is older than `--older-than-days`.
2. It is not among the newest `--keep-latest` traces in the selected scope.

The command is a dry run unless `--execute` is supplied:

```bash
world prune-traces --older-than-days 30 --keep-latest 100
world prune-traces --older-than-days 30 --keep-latest 100 --execute
```

Deleting a command cascades to its execution spans and state diffs. Provider audit,
tool-call, and world-event records remain part of the experiment audit trail.

## REST and WebSocket Service

Start the single-worker observability service:

```bash
world serve --host 127.0.0.1 --port 8000
```

Generate a safe scripted trace when developing the UI or testing an empty database:

```bash
world demo-trace
```

The command initializes an empty database when necessary and then executes one
scripted autonomous turn. It never contacts an external model provider and can be
run repeatedly.

Configure explicit browser origins with `--cors-origins`. Full recorded span and
provider payloads are disabled unless `EMERGENCE_TRACE_PAYLOAD_TOKEN` is set; clients
must send the same value in `X-Trace-Payload-Token` and opt in with
`include_payloads=true`.

Versioned read-only resources are available below `/api/v1`:

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

Live lightweight events are sent through `WS /ws/v1/traces`. Optional `world_id`
and `command_id` query parameters filter subscriptions. `after_sequence` resumes a
connection from a committed outbox cursor. Events emitted in the service process
before the world transaction commits have `provisional=true`; persisted outbox events
are delivered with `provisional=false`, and `command.committed` confirms a successful
commit. `stream.gap` tells a slow client to reconcile through REST or reconnect with
its last committed cursor.

The in-memory broker provides low-latency provisional events. The
`trace_stream_events` database outbox provides committed cross-process delivery, so a
separate `world` CLI process is visible to the service after its transaction commits.
The default command still runs one Uvicorn worker; a shared Redis or PostgreSQL
transport is the appropriate upgrade for high-volume multi-instance deployment.
