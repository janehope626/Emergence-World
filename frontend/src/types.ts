export type TraceSummary = {
  id: string
  world_id: string
  turn_id: string | null
  name: string
  status: string
  started_at: string
  completed_at: string | null
  span_count: number
  state_diff_count: number
  error: string | null
}

export type CommandTrace = Omit<TraceSummary, 'span_count' | 'state_diff_count'> & {
  arguments: Record<string, unknown>
}

export type Span = {
  id: string
  parent_span_id: string | null
  turn_id: string | null
  sequence_number: number
  stage: string
  function_name: string
  source_file: string | null
  source_line: number | null
  status: string
  started_at: string
  completed_at: string | null
  duration_ms: number | null
  error: string | null
  input?: Record<string, unknown> | null
  output?: Record<string, unknown> | null
}

export type ProviderInteraction = {
  sequence_number: number
  provider: string
  model: string
  tool_calls: Record<string, unknown>[]
  latency_ms: number | null
  cost_usd: number | null
  request?: Record<string, unknown> | null
  response?: Record<string, unknown> | null
}

export type ToolCall = { sequence_number: number; tool_name: string; arguments: Record<string, unknown>; status: string; result: Record<string, unknown> | null; error: string | null }
export type WorldEvent = { sequence_number: number; event_type: string; payload: Record<string, unknown> }
export type StateDiff = { sequence_number: number; entity_type: string; entity_id: string; path: string; before: unknown; after: unknown }
export type Page<T> = { offset: number; limit: number; items: T[] }

export type TraceDetails = {
  command: CommandTrace
  spans: Span[]
  providers: ProviderInteraction[]
  tools: ToolCall[]
  events: WorldEvent[]
  diffs: StateDiff[]
}

export type StreamEvent = {
  type: string
  event_id: string
  command_id: string | null
  stream_sequence: number | null
  provisional: boolean
  timestamp: string
  data: Record<string, unknown>
}
