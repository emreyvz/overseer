<script lang="ts">
  // Archive: event history + period type counts (event search + statistics).
  import { onMount } from 'svelte'
  import { timeline } from '../../lib/stores'
  import { api } from '../../lib/api'

  interface Row { ts: number; type: string; label: string; snapshot?: string | null }
  let events = $state<Row[]>([])
  let counts = $state<[string, number][]>([])
  let source = $state('LIVE FEED')
  let lightbox = $state<string | null>(null)
  const SNAP = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'

  function fromTimeline() {
    events = $timeline.slice(0, 60).map((e) => ({ ts: e.ts, type: e.type, label: e.label }))
    const m = new Map<string, number>()
    for (const e of events) m.set(e.type, (m.get(e.type) ?? 0) + 1)
    counts = [...m.entries()].sort((a, b) => b[1] - a[1])
  }

  onMount(async () => {
    const now = Date.now() / 1000
    try {
      const [ev, st] = await Promise.all([api.events(60), api.stats(now - 7 * 86400, now)])
      events = ev.map((e) => ({ ts: e.ts, type: e.type, label: e.label, snapshot: e.snapshot }))
      counts = Object.entries(st).sort((a, b) => b[1] - a[1])
      source = 'ARCHIVE · LAST 7 DAYS'
    } catch {
      fromTimeline()
      source = 'OFFLINE · LIVE FEED'
    }
  })

  let max = $derived(Math.max(1, ...counts.map(([, n]) => n)))
  const hhmmss = (ts: number) => new Date(ts).toLocaleTimeString('tr-TR')
</script>

<section class="archive">
  <div class="hdr caps">/// ARCHIVE <span class="src">· {source}</span> <span class="hint">ESC · POV</span></div>
  <div class="cols">
    <div class="list panel">
      <div class="ph caps">EVENT HISTORY</div>
      {#each events as e}
        <button class="ev caps" class:has={e.snapshot} onclick={() => { if (e.snapshot) lightbox = SNAP + e.snapshot }}>
          <span class="t">{hhmmss(e.ts)}</span><span class="ty">{e.type}</span><span class="lb">{e.label}{#if e.snapshot}<span class="pin"> ◉</span>{/if}</span>
        </button>
      {/each}
      {#if events.length === 0}<div class="empty caps">NO RECORDS</div>{/if}
    </div>

    <div class="stats panel">
      <div class="ph caps">PERIOD SUMMARY</div>
      {#each counts as [type, n]}
        <div class="bar-row caps">
          <span class="bl">{type}</span>
          <span class="track"><span class="fill" style={`width:${(n / max) * 100}%`}></span></span>
          <span class="bn">{n}</span>
        </div>
      {/each}
      {#if counts.length === 0}<div class="empty caps">NO DATA</div>{/if}
    </div>
  </div>

  {#if lightbox}
    <button class="lightbox" aria-label="close" onpointerdown={() => (lightbox = null)}>
      <img src={lightbox} alt="" />
      <span class="lbx caps">◈ EVENT SNAPSHOT · CLICK TO CLOSE</span>
    </button>
  {/if}
</section>

<style>
  .archive { position: absolute; inset: 0; z-index: var(--z-boot); background: #050607; color: var(--ink); padding: 22px 30px; overflow: auto; }
  .hdr { font-size: var(--fs-title); letter-spacing: var(--tracking); margin-bottom: 16px; }
  .hdr .hint { float: right; font-size: var(--fs-micro); color: var(--ink-ghost); }
  .hdr .src { font-size: var(--fs-micro); color: var(--ink-dim); letter-spacing: 0.1em; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .ph { font-size: var(--fs-label); color: var(--scarlet); letter-spacing: var(--tracking); padding: 8px 10px; border-bottom: 1px solid var(--hairline); }
  .list, .stats { max-height: 74vh; overflow-y: auto; }
  .ev { display: grid; grid-template-columns: 80px 110px 1fr; gap: 8px; padding: 4px 10px; font-size: var(--fs-micro); color: var(--ink-dim); width: 100%; text-align: left; cursor: default; }
  .ev.has { cursor: crosshair; } .ev.has:hover { background: #14161a; }
  .ev .t { color: var(--ink-ghost); } .ev .ty { color: var(--ink); }
  .pin { color: var(--scarlet); }
  .lightbox { position: fixed; inset: 0; z-index: var(--z-cmd); background: rgba(0,0,0,0.9); display: grid; place-content: center; cursor: pointer; padding: 0; border: none; }
  .lightbox img { max-width: 90vw; max-height: 82vh; border: 1px solid var(--hairline); }
  .lbx { position: absolute; bottom: 30px; left: 50%; transform: translateX(-50%); font-size: var(--fs-micro); color: var(--ink-dim); letter-spacing: var(--tracking); }
  .bar-row { display: grid; grid-template-columns: 130px 1fr 32px; gap: 8px; align-items: center; padding: 4px 10px; font-size: var(--fs-micro); }
  .track { height: 8px; background: #14161a; } .fill { display: block; height: 100%; background: var(--scarlet); }
  .bn { text-align: right; color: var(--ink); }
  .empty { color: var(--ink-ghost); font-size: var(--fs-label); padding: 20px; text-align: center; }
</style>
