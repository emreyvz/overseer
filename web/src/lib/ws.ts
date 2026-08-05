// OVERSEER — WebSocket client to stores. Degrades to 'offline' (NO SIGNAL) when no backend.
import {
  conn, frame, applyDetections, resetCoast, ooiTargets, system, applyServerCameras, metrics, pushAlert, pushEvent,
  pushDivergence, applyProbeFrame, dreamStatus, coverage, grainTracks,
} from './stores'
import type { WsMessage } from './types'

const WS_URL = (import.meta.env.VITE_WS_URL as string | undefined) ?? 'ws://127.0.0.1:8787/ws'

let socket: WebSocket | null = null
let retry = 0
let stopped = false

function apply(msg: WsMessage) {
  switch (msg.t) {
    case 'frame': frame.set(msg.d); break
    case 'detections': applyDetections(msg.d); break
    case 'ooi': ooiTargets.set(msg.d); break
    case 'alert': pushAlert(msg.d); break
    case 'system': system.set(msg.d); break
    case 'cameras': applyServerCameras(msg.d); break
    case 'conn': if (msg.d !== 'online') resetCoast(); conn.set(msg.d); break
    case 'event': pushEvent(msg.d); break
    case 'metrics': metrics.set(msg.d); break
    // PERCEPTION suite: all four engines publish over the existing socket rather than opening
    // their own — the spectral/status payloads are small and the ordering matters.
    case 'divergence': pushDivergence(msg.d); break
    case 'dream': dreamStatus.set(msg.d); break
    case 'probe': applyProbeFrame(msg.d); break
    case 'grain': grainTracks.update((l) => [msg.d, ...l.filter((x) => x.id !== msg.d.id)].slice(0, 200)); break
    case 'coverage': coverage.set(msg.d); break
  }
}

export function connectWs() {
  stopped = false
  open()
}

function open() {
  if (stopped) return
  conn.set('connecting')
  try {
    socket = new WebSocket(WS_URL)
  } catch {
    scheduleRetry()
    return
  }
  socket.onopen = () => { retry = 0 }
  socket.onmessage = (ev) => {
    try { apply(JSON.parse(ev.data) as WsMessage) } catch { /* ignore malformed */ }
  }
  socket.onclose = () => { conn.set('offline'); scheduleRetry() }
  socket.onerror = () => { socket?.close() }
}

function scheduleRetry() {
  if (stopped) return
  const delay = Math.min(1000 * 2 ** retry, 15000)
  retry += 1
  setTimeout(open, delay)
}

/** Send a command string to the backend (best-effort). */
export function sendCommand(cmd: string) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ t: 'command', d: cmd }))
}

export function disconnectWs() { stopped = true; socket?.close() }
