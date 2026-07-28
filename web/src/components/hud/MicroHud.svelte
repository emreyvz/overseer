<script lang="ts">
  // Top-right micro-HUD: coords · fps · gpu · cpu · ram · REC (brief §4.7).
  import { onMount } from 'svelte'
  import { system, frame, activeCam, cameras, conn } from '../../lib/stores'
  import { occupancy, occupancyPeak, occupancyBand, occupancyHistory, sparklinePoints } from '../../lib/occupancy'
  import { LEX } from '../../lib/lexicon'

  let cam = $derived($cameras.find((c) => c.id === $activeCam))
  let occBand = $derived(occupancyBand($occupancy))
  let spark = $derived(sparklinePoints($occupancyHistory, 34, 10))
  const pct = (n: number) => `%${Math.round(n)}`
  let time = $state('')
  onMount(() => {
    const id = setInterval(() => {
      const d = new Date()
      time = [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')
    }, 1000)
    return () => clearInterval(id)
  })
</script>

<div class="hud caps">
  <span class="dim">COORD</span>
  <span>{cam?.coords ? `${cam.coords[0].toFixed(2)} / ${cam.coords[1].toFixed(2)}` : '--.-- / --.--'}</span>
  <span class="sep">·</span>
  <span class="dim">FPS</span><span>{$frame.fps ? $frame.fps.toFixed(1) : '–'}</span>
  <span class="sep">·</span>
  <span class="dim">GPU</span><span>{$system.gpu == null ? 'N/A' : pct($system.gpu)}</span>
  <span class="sep">·</span>
  <span class="dim">CPU</span><span>{pct($system.cpu)}</span>
  <span class="sep">·</span>
  <span class="dim">RAM</span><span>{pct($system.ram)}</span>
  <span class="sep">·</span>
  <span class="dim">OCC</span><span class="occ occ-{occBand}" title="OCCUPANCY · ANONYMOUS HEADCOUNT">{$occupancy}{#if $occupancyPeak > $occupancy}<span class="pk">/{$occupancyPeak}</span>{/if}</span>
  {#if spark}<svg class="spark" width="34" height="10" viewBox="0 0 34 10" aria-hidden="true"><polyline points={spark} /></svg>{/if}
  {#if $frame.movingCam}<span class="sep">·</span><span class="moving" title="Camera in motion (dashcam) — vehicle speeds are ego-compensated">◈ MOVING CAM</span>{/if}
  <span class="sep">·</span>
  <span class="conn" class:off={$conn === 'offline'}>{LEX.conn[$conn]}</span>
  {#if $system.recActive}<span class="rec">{LEX.rec}</span>{/if}
  <span class="time">{time}</span>
</div>

<style>
  .hud { position: absolute; top: 22px; right: 62px; z-index: var(--z-panel);
    display: flex; align-items: center; gap: 6px; font-size: var(--fs-label); letter-spacing: 0.1em; color: var(--ink); }
  .sep { color: var(--ink-ghost); }
  .conn { color: var(--ink); }
  .conn.off { color: var(--scarlet); }
  .rec { color: var(--scarlet); text-shadow: 0 0 6px var(--scarlet-glow); }
  .moving { color: var(--amber, #d8a200); letter-spacing: 0.12em; }
  /* Neutral by DNA — red stays alarm-only. Density reads via weight/brightness. */
  .occ { color: var(--ink-dim); font-variant-numeric: tabular-nums; }
  .occ-busy { color: var(--ink); }
  .occ-crowded { color: var(--ink); font-weight: 700; text-shadow: 0 0 6px rgba(255,255,255,0.25); }
  .occ .pk { color: var(--ink-ghost); font-weight: 400; }
  .spark { display: block; opacity: 0.7; }
  .spark polyline { fill: none; stroke: var(--ink-dim); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .time { margin-left: 10px; color: var(--ink-dim); }
</style>
