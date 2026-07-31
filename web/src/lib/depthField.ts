// OVERSEER depth field. Pulls a camera's monocular depth (Depth Anything V2, computed backend
// side for the spatial view) and exposes it as a cheap ground-distance lookup. The tactical
// god-view samples it at each subject's foot point so contacts are placed on the radar by REAL
// scene depth + the camera's real FOV, instead of the calibration-free horizon heuristic.
//
// The depth grid covers the full frame in the same normalised coordinates as the detection
// stream, so a track's ground point maps straight onto it. Depth is inverse-depth normalised to
// [0,1] with 1 = nearest; we return 0 (near) .. 1 (far). Prefer bg_depth (ground plane with
// standing objects removed) when the backend shipped it.
import { api } from './api'

export interface DepthField {
  sid: string
  w: number; h: number
  fovRad: number
  ts: number
  distAt(nx: number, ny: number): number   // 0 = at camera .. 1 = far
}

const cache = new Map<string, DepthField>()
const inflight = new Map<string, Promise<DepthField | null>>()
const TTL = 45000                           // depth of a fixed scene is near-static; refresh rarely

function decode(b64: string): Float32Array {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
  return new Float32Array(bytes.buffer)
}

// Latest depth field for a camera, or null if none fetched yet.
export const cachedDepthField = (sid: string): DepthField | null => cache.get(sid) ?? null

// Fetch (and cache) the depth field for a camera. Deduplicates concurrent calls and honours a
// TTL so the heavy depth pass runs at most once per TTL per camera. Returns null when the
// backend has no scene (disabled / no frame / depth model unavailable / SIM).
export async function ensureDepthField(sid: string, grid = 240): Promise<DepthField | null> {
  const c = cache.get(sid)
  if (c && performance.now() - c.ts < TTL) return c
  const pending = inflight.get(sid)
  if (pending) return pending
  const p = (async (): Promise<DepthField | null> => {
    try {
      const res = await api.spatial(sid, grid)
      const s = res?.scene
      if (!s || !s.w || !s.h) return null
      const disp = decode(s.bg_depth ?? s.depth)
      if (disp.length < s.w * s.h) return null
      const w = s.w, h = s.h
      const field: DepthField = {
        sid, w, h, fovRad: ((s.fov || 60) * Math.PI) / 180, ts: performance.now(),
        distAt(nx, ny) {
          const x = Math.min(w - 1, Math.max(0, Math.round(nx * (w - 1))))
          const y = Math.min(h - 1, Math.max(0, Math.round(ny * (h - 1))))
          const d01 = disp[y * w + x]
          const near = Number.isFinite(d01) ? Math.min(1, Math.max(0, d01)) : 0.5
          return 1 - near     // invert: nearest(1) -> 0, farthest(0) -> 1
        },
      }
      cache.set(sid, field)
      return field
    } catch {
      return null
    } finally {
      inflight.delete(sid)
    }
  })()
  inflight.set(sid, p)
  return p
}
