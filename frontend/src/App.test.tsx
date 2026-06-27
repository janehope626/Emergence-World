import { render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('./useTraceStream', () => ({ useTraceStream: () => ({ connected: true, events: [] }) }))
vi.mock('./api', () => ({
  listTraces: vi.fn(async () => ({ offset: 0, limit: 100, items: [] })),
  loadTrace: vi.fn(),
}))

beforeEach(() => localStorage.clear())

it('shows an actionable empty trace state', async () => {
  render(<App />)
  expect(await screen.findByText('No traces yet')).toBeInTheDocument()
  expect(screen.getAllByText('world demo-trace')).toHaveLength(2)
  expect(screen.getByText('Live stream connected')).toBeInTheDocument()
})
