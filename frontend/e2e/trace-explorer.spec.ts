import { expect, test } from '@playwright/test'

const command = { id: 'cmd-demo', world_id: 'world-demo', turn_id: 'turn-demo', name: 'run-autonomous', status: 'completed', started_at: '2026-06-22T10:00:00Z', completed_at: '2026-06-22T10:00:00.120Z', error: null }
const page = (items: unknown[]) => ({ offset: 0, limit: 500, items })

test('selects a trace and navigates execution resources', async ({ page: browser }) => {
  await browser.route('**/api/v1/traces?**', (route) => route.fulfill({ json: page([{ ...command, span_count: 2, state_diff_count: 1 }]) }))
  await browser.route('**/api/v1/traces/cmd-demo', (route) => route.fulfill({ json: { ...command, arguments: { minutes: 30 } } }))
  await browser.route('**/api/v1/traces/cmd-demo/spans?**', (route) => route.fulfill({ json: page([
    { id: 'one', parent_span_id: null, turn_id: 'turn-demo', sequence_number: 1, stage: 'provider', function_name: 'complete', source_file: 'agents/runtime.py', source_line: 1, status: 'completed', started_at: command.started_at, completed_at: command.completed_at, duration_ms: 90, error: null },
    { id: 'two', parent_span_id: 'one', turn_id: 'turn-demo', sequence_number: 2, stage: 'tool_handler', function_name: 'go_to_place', source_file: 'tools/core.py', source_line: 1, status: 'completed', started_at: command.started_at, completed_at: command.completed_at, duration_ms: 30, error: null },
  ]) }))
  await browser.route('**/api/v1/traces/cmd-demo/provider-interactions?**', (route) => route.fulfill({ json: page([{ sequence_number: 1, provider: 'scripted', model: 'deterministic-script-v1', tool_calls: [], latency_ms: 2, cost_usd: 0 }]) }))
  await browser.route('**/api/v1/traces/cmd-demo/tool-calls?**', (route) => route.fulfill({ json: page([{ sequence_number: 1, tool_name: 'go_to_place', arguments: { place: 'Central Plaza' }, status: 'succeeded', result: { moved: true }, error: null }]) }))
  await browser.route('**/api/v1/traces/cmd-demo/events?**', (route) => route.fulfill({ json: page([]) }))
  await browser.route('**/api/v1/traces/cmd-demo/state-diffs?**', (route) => route.fulfill({ json: page([{ sequence_number: 1, entity_type: 'agent_state', entity_id: 'agent-one', path: '/location', before: 'Home', after: 'Central Plaza' }]) }))
  await browser.goto('/')
  await expect(browser.getByRole('heading', { name: 'run-autonomous' })).toBeVisible()
  await expect(browser.getByText('go_to_place')).toBeVisible()
  await browser.getByRole('button', { name: /Provider/ }).click()
  await expect(browser.getByText('provider interaction #1')).toBeVisible()
  await browser.getByRole('button', { name: /State diff/ }).click()
  await expect(browser.getByText('/location')).toBeVisible()
})
