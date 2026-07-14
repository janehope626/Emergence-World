// 封装追踪查询 API，并组合一次命令执行的完整明细。
import type { CommandTrace, Page, ProviderInteraction, Span, StateDiff, ToolCall, TraceDetails, TraceSummary, WorldEvent } from './types'

const API = import.meta.env.VITE_API_BASE ?? '/api/v1'

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API}${path}`, { signal })
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return response.json() as Promise<T>
}

export type TraceFilters = { world?: string; stage?: string; status?: string }

export async function listTraces(filters: TraceFilters = {}, signal?: AbortSignal): Promise<Page<TraceSummary>> {
  const params = new URLSearchParams({ limit: '100' })
  if (filters.world) params.set('world_id', filters.world)
  if (filters.stage) params.set('stage', filters.stage)
  if (filters.status) params.set('status', filters.status)
  return get(`/traces?${params}`, signal)
}

export async function loadTrace(commandId: string, signal?: AbortSignal): Promise<TraceDetails> {
  const root = `/traces/${encodeURIComponent(commandId)}`
  const [command, spans, providers, tools, events, diffs] = await Promise.all([
    get<CommandTrace>(root, signal),
    get<Page<Span>>(`${root}/spans?limit=500`, signal),
    get<Page<ProviderInteraction>>(`${root}/provider-interactions?limit=500`, signal),
    get<Page<ToolCall>>(`${root}/tool-calls?limit=500`, signal),
    get<Page<WorldEvent>>(`${root}/events?limit=500`, signal),
    get<Page<StateDiff>>(`${root}/state-diffs?limit=500`, signal),
  ])
  return { command, spans: spans.items, providers: providers.items, tools: tools.items, events: events.items, diffs: diffs.items }
}
