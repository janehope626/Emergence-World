import { afterEach, describe, expect, it, vi } from 'vitest'
import { listTraces, loadTrace } from './api'

afterEach(() => vi.restoreAllMocks())

describe('trace API', () => {
  it('encodes filters and loads bounded summaries', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ offset: 0, limit: 100, items: [] }), { status: 200 }))
    await listTraces({ world: 'world one', stage: 'provider', status: 'completed' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/traces?limit=100&world_id=world+one&stage=provider&status=completed', { signal: undefined })
  })

  it('loads command resources independently', async () => {
    const pages = new Map([
      ['/api/v1/traces/cmd', { id: 'cmd', arguments: {} }],
      ['/api/v1/traces/cmd/spans?limit=500', { items: [{ id: 'span' }] }],
      ['/api/v1/traces/cmd/provider-interactions?limit=500', { items: [] }],
      ['/api/v1/traces/cmd/tool-calls?limit=500', { items: [] }],
      ['/api/v1/traces/cmd/events?limit=500', { items: [] }],
      ['/api/v1/traces/cmd/state-diffs?limit=500', { items: [] }],
    ])
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => new Response(JSON.stringify(pages.get(String(input))), { status: 200 }))
    const result = await loadTrace('cmd')
    expect(result.command.id).toBe('cmd')
    expect(result.spans).toHaveLength(1)
  })
})
