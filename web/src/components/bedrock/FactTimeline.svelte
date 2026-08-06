<script lang="ts">
  // BEDROCK — the timeline.
  //
  // A Gantt of reality: one lane per entity, one bar per fact, spanning the interval the fact
  // was true for. Confidence is opacity plus an underline whose length IS the confidence, so a
  // 0.4 colour claim is visibly thinner than a 0.9 plate read and nobody mistakes one for the
  // other.
  import { FAMILY_COLOUR, FAMILY_ROW, lanes, predMeta, vocab } from '../../lib/bedrock'
  import type { BedrockFact, BedrockResult } from '../../lib/types'

  let { result, selected, onselect, onhover }: {
    result: BedrockResult
    selected: number | null
    onselect: (f: BedrockFact) => void
    onhover: (f: BedrockFact | null) => void
  } = $props()

  let zoom = $state(1)
  let pan = $state(0)
  let wrap = $state<HTMLElement>()
  let dragFrom: { x: number; pan: number } | null = null

  const v = $derived($vocab)
  const rows = $derived(lanes(result))
  const t0 = $derived(result.window.from)
  const t1 = $derived(result.window.to)
  const span = $derived(Math.max(1, t1 - t0))

  // visible window after zoom/pan, in fractions of the query window
  const view = $derived.by(() => {
    const w = 1 / zoom
    const from = Math.max(0, Math.min(1 - w, pan))
    return { from, to: from + w }
  })

  const x = (ts: number) => ((ts - t0) / span - view.from) / (view.to - view.from) * 100
  const wOf = (f: BedrockFact) => {
    const end = f.valid_to ?? t1
    return Math.max(0.35, x(end) - x(f.valid_from))
  }
  const family = (pred: string) => predMeta(v, pred)?.family ?? 'presence'

  function onWheel(e: WheelEvent) {
    e.preventDefault()
    const before = view.from + (e.offsetX / (wrap?.clientWidth || 1)) * (view.to - view.from)
    zoom = Math.max(1, Math.min(120, zoom * (e.deltaY < 0 ? 1.25 : 0.8)))
    const w = 1 / zoom
    pan = Math.max(0, Math.min(1 - w, before - w / 2))
  }
  function onDown(e: PointerEvent) { dragFrom = { x: e.clientX, pan } }
  function onMove(e: PointerEvent) {
    if (!dragFrom || !wrap) return
    const dx = (e.clientX - dragFrom.x) / wrap.clientWidth
    const w = 1 / zoom
    pan = Math.max(0, Math.min(1 - w, dragFrom.pan - dx * w))
  }
  function onUp() { dragFrom = null }
  function fit() { zoom = 1; pan = 0 }

  const label = (f: BedrockFact) => {
    const m = predMeta(v, f.pred)
    return `${(m?.label ?? f.pred).toUpperCase()}${f.val ? ` ${f.val.toUpperCase()}` : ''}`
  }
  const tickCount = 6
  const ticks = $derived(Array.from({ length: tickCount + 1 }, (_, i) => {
    const frac = view.from + (i / tickCount) * (view.to - view.from)
    return { pct: (i / tickCount) * 100, ts: t0 + frac * span }
  }))
  const stamp = (ts: number) => {
    const d = new Date(ts)
    return span > 3 * 86_400_000
      ? d.toLocaleDateString(undefined, { day: '2-digit', month: 'short' })
      : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  }
</script>

<div class="tl">
  <div class="axis caps">
    {#each ticks as t}
      <span class="ax" style={`left:${t.pct}%`}>{stamp(t.ts)}</span>
    {/each}
    {#if zoom > 1}<button class="fit caps" onclick={fit}>⤢ FIT</button>{/if}
  </div>

  <!-- density ribbon doubles as the zoom minimap -->
  <div class="ribbon">
    {#each Array(60) as _, i}
      {@const lo = t0 + (i / 60) * span}
      {@const hi = t0 + ((i + 1) / 60) * span}
      {@const n = result.facts.filter((f) => f.valid_from < hi && (f.valid_to ?? t1) > lo).length}
      <span class="rb" style={`height:${Math.min(100, n * 14)}%`}></span>
    {/each}
    <span class="rview" style={`left:${view.from * 100}%; width:${(view.to - view.from) * 100}%`}></span>
  </div>

  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="grid" bind:this={wrap} onwheel={onWheel} onpointerdown={onDown}
    onpointermove={onMove} onpointerup={onUp} onpointerleave={onUp}>
    {#each rows as row (row.entity.uid)}
      <div class="lane">
        <div class="gutter">
          {#if row.entity.snapshot}<img class="thumb" src={row.entity.snapshot} alt="" />{:else}
            <span class="thumb ph caps">{row.entity.kind.slice(0, 2)}</span>{/if}
          <span class="ename caps">{row.entity.label || row.entity.ref}</span>
        </div>
        <div class="bars">
          {#each row.facts as f (f.id)}
            <button class="bar r{FAMILY_ROW[family(f.pred)] ?? 0}"
              class:sel={selected === f.id}
              class:open={f.valid_to === null}
              class:retracted={f.tx_to !== null}
              style={`left:${x(f.valid_from)}%; width:${wOf(f)}%;
                      --fc:${FAMILY_COLOUR[family(f.pred)] ?? 'var(--ink-dim)'};
                      opacity:${0.35 + f.conf * 0.65}`}
              onclick={() => onselect(f)}
              onmouseenter={() => onhover(f)} onmouseleave={() => onhover(null)}
              title={label(f)}>
              <span class="conf" style={`width:${Math.round(f.conf * 100)}%`}></span>
            </button>
          {/each}
        </div>
      </div>
    {/each}
    {#if !rows.length}
      <div class="none caps">NO FACTS IN THIS WINDOW</div>
    {/if}
  </div>

  <div class="legend caps">
    {#each Object.entries(FAMILY_COLOUR) as [fam, col]}
      <span class="lg"><span class="ldot" style={`background:${col}`}></span>{fam}</span>
    {/each}
    <span class="spacer"></span>
    <span class="lg dim">BAR OPACITY AND UNDERLINE = CONFIDENCE</span>
  </div>
</div>

<style>
  .tl { display: flex; flex-direction: column; min-height: 0; height: 100%; }
  .axis { position: relative; height: 18px; border-bottom: 1px solid var(--hairline); flex: 0 0 auto; }
  .ax { position: absolute; top: 3px; transform: translateX(-50%); font-size: 10px;
    color: var(--ink-ghost); letter-spacing: 0.1em; white-space: nowrap; }
  .fit { position: absolute; right: 6px; top: 1px; padding: 2px 6px; border: 1px solid var(--hairline);
    background: #04070a; color: var(--ink-dim); font-size: 10px; letter-spacing: 0.12em; cursor: crosshair; }
  .fit:hover { color: var(--cyan); border-color: var(--cyan); }

  .ribbon { position: relative; display: flex; align-items: flex-end; gap: 1px; height: 26px;
    padding: 0 0 0 148px; border-bottom: 1px solid var(--hairline); flex: 0 0 auto; }
  .rb { flex: 1; background: var(--ink-ghost); opacity: 0.5; min-height: 1px; }
  .rview { position: absolute; left: 148px; top: 0; bottom: 0; border: 1px solid var(--cyan);
    background: rgba(56,208,227,0.07); pointer-events: none; }

  .grid { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; cursor: grab; }
  .grid:active { cursor: grabbing; }
  .lane { display: grid; grid-template-columns: 148px 1fr; align-items: center; height: 34px;
    border-bottom: 1px solid rgba(236,236,236,0.05); }
  .gutter { display: flex; align-items: center; gap: 7px; padding: 0 8px; min-width: 0; }
  .thumb { width: 24px; height: 18px; object-fit: cover; border: 1px solid var(--hairline); flex: 0 0 auto; }
  .thumb.ph { display: flex; align-items: center; justify-content: center; font-size: 10px;
    color: var(--ink-ghost); letter-spacing: 0.06em; }
  .ename { font-size: 11px; color: var(--ink-dim); letter-spacing: 0.08em; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
  .bars { position: relative; height: 100%; }
  .bar { position: absolute; height: 8px; padding: 0; border: none; background: var(--fc);
    cursor: crosshair; }
  .bar.r0 { bottom: 3px; } .bar.r1 { bottom: 12px; } .bar.r2 { bottom: 21px; }
  .bar:hover { filter: brightness(1.6); }
  .bar.sel { outline: 1px solid var(--cyan); outline-offset: 1px; }
  /* an open interval fades out rather than ending in a hard cap: it has not finished */
  .bar.open { mask-image: linear-gradient(90deg, #000 62%, transparent 100%); }
  .bar.retracted { opacity: 0.35 !important; background: repeating-linear-gradient(
    45deg, var(--amber) 0 2px, transparent 2px 4px); }
  .conf { position: absolute; left: 0; bottom: -2px; height: 1px; background: currentColor;
    opacity: 0.9; }

  .none { padding: 30px; text-align: center; color: var(--ink-ghost); font-size: 11px; letter-spacing: 0.14em; }
  .legend { display: flex; align-items: center; gap: 12px; padding: 6px 10px; flex: 0 0 auto;
    border-top: 1px solid var(--hairline); font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.12em; }
  .lg { display: inline-flex; align-items: center; gap: 5px; }
  .ldot { width: 6px; height: 3px; }
  .spacer { flex: 1; } .dim { color: var(--ink-ghost); opacity: 0.7; }
</style>
