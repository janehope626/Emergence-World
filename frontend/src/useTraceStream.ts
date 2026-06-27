import { useEffect, useRef, useState } from 'react'
import type { StreamEvent } from './types'

const storageKey = 'emergence.trace.stream-sequence'

export function useTraceStream(onReconcile: () => void) {
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState<StreamEvent[]>([])
  const callback = useRef(onReconcile)
  callback.current = onReconcile

  useEffect(() => {
    let stopped = false
    let socket: WebSocket | undefined
    let retry: number | undefined
    const connect = () => {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const cursor = localStorage.getItem(storageKey) ?? '0'
      const configured = import.meta.env.VITE_WS_URL as string | undefined
      socket = new WebSocket(configured ?? `${protocol}//${location.host}/ws/v1/traces?after_sequence=${cursor}`)
      socket.onopen = () => setConnected(true)
      socket.onclose = () => {
        setConnected(false)
        if (!stopped) retry = window.setTimeout(connect, 1500)
      }
      socket.onmessage = ({ data }) => {
        const event = JSON.parse(data) as StreamEvent
        if (event.stream_sequence != null && !event.provisional) localStorage.setItem(storageKey, String(event.stream_sequence))
        setEvents((current) => [...current.slice(-99), event])
        if (event.type === 'stream.gap' || event.type === 'command.committed') callback.current()
      }
    }
    connect()
    return () => {
      stopped = true
      if (retry) clearTimeout(retry)
      socket?.close()
    }
  }, [])

  return { connected, events }
}
