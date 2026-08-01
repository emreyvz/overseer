<!-- Cross-camera alerts board: every alert from every camera in one place, independent of which
     feed is open. Filter by severity, search, and act on each one (jump to its camera, open a
     case). The DominantRail only ever features the single top incident on POV; this is the full
     ledger. -->
<script lang="ts">
  import { alerts, cameras, activeCam, mode, stage, investigateCase, flashBanner } from '../lib/stores'
  import { sendCommand } from '../lib/ws'
  import { SIM } from '../lib/sim'
  import { api } from '../lib/api'
  import { sfx } from '../lib/audio'

  let { onclose }: { onclose: () => void } = $props()

  let sev = $state<'all' | 'critical' | 'warning' | 'info'>('all')
  let q = $state('')
  let busy = $state(false)

  const rows = $derived(
    $alerts
      .filter((a) => sev === 'all' || a.severity === sev)
      .filter((a) => {
        const s = q.trim().toLowerCase()
        return !s || `${a.type} ${a.cam} ${a.summary}`.toLowerCase().includes(s)
      }),
  )
  const counts = $derived({
    all: $alerts.length,
    critical: $alerts.filter((a) => a.severity === 'critical').length,
    warning: $alerts.filter((a) => a.severity === 'warning').length,
    info: $alerts.filter((a) => a.severity === 'info').length,
  })

  const clock = (ts: number) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  function goToCamera(name: string) {
    const cam = $cameras.find((c) => c.name === name) ?? $cameras.find((c) => c.name.toLowerCase() === name.toLowerCase())
    if (!cam) { flashBanner('camera not found', true, 1200); return }
    stage.set('live'); mode.set('pov')
    activeCam.set(cam.id)
    if (!SIM) sendCommand(`connect:${cam.name}`)
    sfx('glitch')
    onclose()
  }

  async function openCase(a: (typeof rows)[number]) {
    if (busy) return
    busy = true
    try {
      const c = await api.caseFromAlert({ ts: String(a.ts), severity: a.severity, type: a.type, cam: a.cam, summary: a.summary })
      if (c?.id != null) { investigateCase.set(c.id); stage.set('live'); mode.set('case'); onclose() }
    } catch { flashBanner('could not open case', true, 1200) } finally { busy = false }
  }
</script>

<div class="board" role="dialog" aria-label="All alerts">
  <header>
    <div class="ttl caps">ALERTS <span class="all">· ALL CAMERAS</span></div>
    <div class="filters caps">
      {#each (['all', 'critical', 'warning', 'info'] as const) as k}
        <button class="chip {sev === k ? 'on' : ''} {k}" onclick={() => (sev = k)}>{k}<span class="n">{counts[k]}</span></button>
      {/each}
    </div>
    <input class="search" placeholder="filter alerts…" bind:value={q} />
    <button class="x" onclick={onclose} aria-label="Close">ESC</button>
  </header>

  <div class="list">
    {#if rows.length === 0}
      <div class="empty caps">NO ALERTS{q || sev !== 'all' ? ' MATCH THIS FILTER' : ' YET'}</div>
    {:else}
      {#each rows as a (a.ts + a.type + a.cam)}
        <div class="row {a.severity}">
          <span class="dot"></span>
          <span class="time">{clock(a.ts)}</span>
          <span class="type caps">{a.type}</span>
          <button class="cam" onclick={() => goToCamera(a.cam)} title="Go to this camera">{a.cam}</button>
          {#if (a.hits ?? 1) > 1}<span class="hits">×{a.hits}</span>{/if}
          <span class="summary">{a.summary}</span>
          {#if a.threat}<span class="thr">{a.threat}</span>{/if}
          <span class="acts">
            <button onclick={() => goToCamera(a.cam)}>GO</button>
            <button onclick={() => openCase(a)} disabled={busy}>CASE</button>
          </span>
        </div>
      {/each}
    {/if}
  </div>
</div>

<style>
  .board { position: fixed; inset: 0; z-index: 260; background: rgba(6, 8, 10, 0.94);
    backdrop-filter: blur(3px); display: flex; flex-direction: column; color: var(--ink); }
  header { display: flex; align-items: center; gap: 18px; padding: 16px 22px; border-bottom: 1px solid rgba(255,255,255,0.08); }
  .ttl { font-size: 15px; letter-spacing: 0.22em; }
  .ttl .all { color: var(--ink-dim); }
  .filters { display: flex; gap: 8px; }
  .chip { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.12); color: var(--ink-dim);
    padding: 4px 10px; font: inherit; font-size: 11px; letter-spacing: 0.16em; cursor: pointer; display: inline-flex; gap: 7px; }
  .chip .n { color: var(--ink); opacity: 0.7; }
  .chip:hover { color: var(--ink); }
  .chip.on { color: var(--ink); border-color: var(--ink); }
  .chip.on.critical { color: var(--scarlet); border-color: var(--scarlet); }
  .chip.on.warning { color: #e0a02e; border-color: #e0a02e; }
  .search { margin-left: auto; background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.14);
    color: var(--ink); padding: 6px 12px; font: inherit; font-size: 12px; width: 220px; }
  .x { background: none; border: 1px solid var(--ink-dim); color: var(--ink-dim); padding: 5px 10px;
    font: inherit; font-size: 11px; letter-spacing: 0.12em; cursor: pointer; }
  .x:hover { color: var(--scarlet); border-color: var(--scarlet); }

  .list { flex: 1; overflow-y: auto; padding: 8px 0; }
  .row { display: flex; align-items: center; gap: 14px; padding: 10px 22px; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13px; }
  .row:hover { background: rgba(255,255,255,0.03); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink-dim); flex: 0 0 auto; }
  .row.critical .dot { background: var(--scarlet); box-shadow: 0 0 8px var(--scarlet); }
  .row.warning .dot { background: #e0a02e; }
  .row.info .dot { background: var(--cyan); }
  .time { color: var(--ink-dim); font-variant-numeric: tabular-nums; flex: 0 0 auto; }
  .type { flex: 0 0 auto; min-width: 150px; letter-spacing: 0.12em; }
  .row.critical .type { color: var(--scarlet); }
  .cam { background: none; border: none; color: var(--cyan); font: inherit; cursor: pointer; padding: 0; flex: 0 0 auto; }
  .cam:hover { text-decoration: underline; }
  .hits { color: #e0a02e; font-size: 11px; flex: 0 0 auto; }
  .summary { color: var(--ink-dim); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .thr { color: var(--ink-dim); font-size: 11px; opacity: 0.6; flex: 0 0 auto; }
  .acts { display: flex; gap: 6px; flex: 0 0 auto; }
  .acts button { background: none; border: 1px solid rgba(255,255,255,0.16); color: var(--ink-dim);
    font: inherit; font-size: 10px; letter-spacing: 0.1em; padding: 3px 8px; cursor: pointer; }
  .acts button:hover:not(:disabled) { color: var(--ink); border-color: var(--ink); }
  .acts button:disabled { opacity: 0.4; cursor: default; }
  .empty { color: var(--ink-dim); text-align: center; padding: 60px; letter-spacing: 0.2em; font-size: 12px; }
</style>
