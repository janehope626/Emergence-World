import { useCallback, useEffect, useMemo, useState } from 'react'
import Editor from '@monaco-editor/react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { Background, Controls, MiniMap, ReactFlow, type Edge, type Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { listTraces, loadTrace, type TraceFilters } from './api'
import type { Span, TraceDetails, TraceSummary } from './types'
import { useTraceStream } from './useTraceStream'
import './styles.css'

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const stages = ['', 'scheduler', 'needs', 'context', 'provider', 'tool_validation', 'tool_handler', 'event', 'state_diff', 'clock']
type Tab = 'graph' | 'provider' | 'tools' | 'events' | 'diffs' | 'json'

function duration(start: string, end: string | null) {
  if (!end) return 'running'
  return `${Math.max(0, new Date(end).getTime() - new Date(start).getTime())} ms`
}

function Status({ value, provisional = false }: { value: string; provisional?: boolean }) {
  return <span className={`status status-${value} ${provisional ? 'provisional' : ''}`}>{provisional ? 'provisional' : value}</span>
}

function TraceList({ traces, selected, onSelect }: { traces: TraceSummary[]; selected?: string; onSelect: (id: string) => void }) {
  return <div className="trace-list" aria-label="Trace list">
    {traces.length === 0 && <div className="empty"><b>No traces yet</b><span>Run <code>world demo-trace</code>, then refresh.</span></div>}
    {traces.map((trace) => <button className={`trace-row ${selected === trace.id ? 'selected' : ''}`} key={trace.id} onClick={() => onSelect(trace.id)}>
      <span className="trace-row-top"><b>{trace.name}</b><Status value={trace.status} /></span>
      <span className="muted mono">{trace.id.slice(0, 12)}</span>
      <span className="trace-stats"><span>{trace.span_count} spans</span><span>{trace.state_diff_count} changes</span></span>
      <time>{new Date(trace.started_at).toLocaleString()}</time>
    </button>)}
  </div>
}

function SpanGraph({ spans, onInspect }: { spans: Span[]; onInspect: (span: Span) => void }) {
  const { nodes, edges } = useMemo(() => {
    const depth = new Map<string, number>()
    const counters = new Map<number, number>()
    const graphNodes: Node[] = spans.map((span) => {
      const parentDepth = span.parent_span_id ? depth.get(span.parent_span_id) ?? 0 : -1
      const level = parentDepth + 1
      depth.set(span.id, level)
      const row = counters.get(level) ?? 0
      counters.set(level, row + 1)
      return {
        id: span.id,
        position: { x: level * 260, y: row * 110 },
        data: { label: <button className="node-content" onClick={() => onInspect(span)}><small>{span.stage}</small><b>{span.function_name}</b><span>{span.duration_ms?.toFixed(1) ?? '—'} ms</span></button> },
        className: `flow-node ${span.status}`,
      }
    })
    const graphEdges: Edge[] = spans.filter((span) => span.parent_span_id).map((span) => ({ id: `e-${span.id}`, source: span.parent_span_id!, target: span.id, animated: span.status === 'running' }))
    return { nodes: graphNodes, edges: graphEdges }
  }, [spans, onInspect])
  return <div className="graph"><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false} nodesConnectable={false}>
    <Background color="#1d4537" gap={24} /><MiniMap pannable zoomable /><Controls />
  </ReactFlow></div>
}

function Metrics({ details }: { details: TraceDetails }) {
  const stageDurations = new Map<string, number>()
  details.spans.forEach((span) => stageDurations.set(span.stage, (stageDurations.get(span.stage) ?? 0) + (span.duration_ms ?? 0)))
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 50, right: 18, top: 15, bottom: 55 },
    xAxis: { type: 'category', data: [...stageDurations.keys()], axisLabel: { color: '#8fa9a0', rotate: 28 }, axisLine: { lineStyle: { color: '#315a4b' } } },
    yAxis: { type: 'value', name: 'ms', nameTextStyle: { color: '#8fa9a0' }, axisLabel: { color: '#8fa9a0' }, splitLine: { lineStyle: { color: '#17362c' } } },
    tooltip: { trigger: 'axis' },
    series: [{ type: 'bar', data: [...stageDurations.values()].map((n) => Number(n.toFixed(2))), itemStyle: { color: '#56e8aa', borderRadius: [4, 4, 0, 0] } }],
  }
  return <div className="metrics">
    <div><b>{details.spans.length}</b><span>Spans</span></div><div><b>{details.tools.length}</b><span>Tool calls</span></div><div><b>{details.events.length}</b><span>World events</span></div><div><b>{details.diffs.length}</b><span>State changes</span></div>
    <ReactEChartsCore echarts={echarts} option={option} className="chart" />
  </div>
}

function Records({ records, label }: { records: unknown[]; label: string }) {
  if (!records.length) return <div className="empty"><b>No {label}</b><span>This command did not produce this resource.</span></div>
  return <div className="records">{records.map((record, index) => <details key={index} open={index === 0}><summary>{label} #{index + 1}</summary><pre>{JSON.stringify(record, null, 2)}</pre></details>)}</div>
}

function Detail({ details }: { details: TraceDetails }) {
  const [tab, setTab] = useState<Tab>('graph')
  const [inspected, setInspected] = useState<unknown>(details.command)
  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: 'graph', label: 'Call graph', count: details.spans.length }, { id: 'provider', label: 'Provider', count: details.providers.length }, { id: 'tools', label: 'Tools', count: details.tools.length }, { id: 'events', label: 'Events', count: details.events.length }, { id: 'diffs', label: 'State diff', count: details.diffs.length }, { id: 'json', label: 'JSON' },
  ]
  return <main className="detail">
    <header className="detail-header"><div><span className="eyebrow">Command execution</span><h2>{details.command.name}</h2><p className="mono muted">{details.command.id}</p></div><div className="command-meta"><Status value={details.command.status} /><span>{duration(details.command.started_at, details.command.completed_at)}</span></div></header>
    <Metrics details={details} />
    <nav className="tabs">{tabs.map((item) => <button key={item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}>{item.label}{item.count != null && <em>{item.count}</em>}</button>)}</nav>
    <section className="tab-panel">
      {tab === 'graph' && <SpanGraph spans={details.spans} onInspect={(span) => { setInspected(span); setTab('json') }} />}
      {tab === 'provider' && <Records records={details.providers} label="provider interaction" />}
      {tab === 'tools' && <Records records={details.tools} label="tool call" />}
      {tab === 'events' && <Records records={details.events} label="world event" />}
      {tab === 'diffs' && <div className="diff-table"><div className="diff-head"><span>Entity</span><span>Path</span><span>Before</span><span>After</span></div>{details.diffs.map((diff) => <div className="diff-row" key={`${diff.sequence_number}-${diff.path}`}><span>{diff.entity_type}<small>{diff.entity_id.slice(0, 8)}</small></span><code>{diff.path}</code><pre>{JSON.stringify(diff.before)}</pre><pre>{JSON.stringify(diff.after)}</pre></div>)}</div>}
      {tab === 'json' && <div className="editor"><Editor height="100%" defaultLanguage="json" theme="vs-dark" value={JSON.stringify(inspected, null, 2)} options={{ readOnly: true, minimap: { enabled: false }, wordWrap: 'on', fontSize: 13 }} /></div>}
    </section>
  </main>
}

export default function App() {
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [selected, setSelected] = useState<string>()
  const [details, setDetails] = useState<TraceDetails>()
  const [filters, setFilters] = useState<TraceFilters>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()
  const refresh = useCallback(async () => {
    try {
      const result = await listTraces(filters)
      setTraces(result.items)
      setSelected((current) => current ?? result.items[0]?.id)
      setError(undefined)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }, [filters])
  const stream = useTraceStream(refresh)
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!selected) { setDetails(undefined); return }
    const controller = new AbortController()
    setLoading(true)
    loadTrace(selected, controller.signal).then(setDetails).catch((reason) => { if (reason.name !== 'AbortError') setError(String(reason)) }).finally(() => setLoading(false))
    return () => controller.abort()
  }, [selected, traces])

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">EW</span><div><h1>World Observatory</h1><p>Execution intelligence</p></div></div>
      <div className="live"><i className={stream.connected ? 'online' : ''} />{stream.connected ? 'Live stream connected' : 'Reconnecting stream'}<span>{stream.events.filter((e) => e.provisional).length} provisional</span></div>
      <div className="filters"><input aria-label="World filter" placeholder="World ID" value={filters.world ?? ''} onChange={(event) => setFilters({ ...filters, world: event.target.value })} /><select aria-label="Stage filter" value={filters.stage ?? ''} onChange={(event) => setFilters({ ...filters, stage: event.target.value })}>{stages.map((stage) => <option key={stage} value={stage}>{stage || 'All stages'}</option>)}</select><select aria-label="Status filter" value={filters.status ?? ''} onChange={(event) => setFilters({ ...filters, status: event.target.value })}><option value="">All statuses</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="running">Running</option></select></div>
      <div className="list-title"><span>Recent traces</span><button onClick={() => void refresh()} aria-label="Refresh traces">↻</button></div>
      <TraceList traces={traces} selected={selected} onSelect={setSelected} />
    </aside>
    {error && <div className="error-banner">{error}<button onClick={() => setError(undefined)}>×</button></div>}
    {loading && !details ? <div className="loading">Loading execution…</div> : details ? <Detail details={details} /> : <div className="welcome"><span className="orb" /><h2>Observe every decision.</h2><p>Generate a demo trace or run an autonomous turn to inspect the complete execution path.</p><code>world demo-trace</code></div>}
  </div>
}
