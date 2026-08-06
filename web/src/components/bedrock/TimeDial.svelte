<script lang="ts">
  // BEDROCK — the dual-time control.
  //
  // The top track is VALID time: when things were true. The bottom, thinner, amber track is
  // TRANSACTION time: when we came to believe them. Dragging the second one rewinds the
  // system's KNOWLEDGE, and facts it had not yet formed retract off the timeline while ones it
  // has since retracted fade back in.
  //
  // This is an audit requirement rendered as the most interesting control on the screen.
  import { WINDOWS } from '../../lib/bedrock'
  import { sfx } from '../../lib/audio'
  import type { BedrockQuery } from '../../lib/types'
  import Explain from '../Explain.svelte'

  let { query, asOf, oldest, delta, onwindow, onasof }: {
    query: BedrockQuery
    asOf: number | null
    oldest: number | null
    delta: number
    onwindow: (from: number, to: number) => void
    onasof: (ms: number | null) => void
  } = $props()

  let vt = $state<HTMLElement>()
  let tt = $state<HTMLElement>()
  let drag = $state<'valid-from' | 'valid-to' | 'tx' | null>(null)

  const now = Date.now()
  const txFrom = $derived(oldest ?? now - 30 * 86_400_000)
  const txSpan = $derived(Math.max(1, now - txFrom))
  const txPct = $derived(asOf === null ? 100 : ((asOf - txFrom) / txSpan) * 100)

  // the valid track spans a generous frame around the query window so the brush can be moved
  const vFrom = $derived(Math.min(query.window?.from ?? now - 86_400_000, txFrom))
  const vSpan = $derived(Math.max(1, now - vFrom))
  const bFrom = $derived((((query.window?.from ?? now) - vFrom) / vSpan) * 100)
  const bTo = $derived((((query.window?.to ?? now) - vFrom) / vSpan) * 100)

  function pos(e: PointerEvent, el: HTMLElement | undefined): number {
    if (!el) return 0
    const r = el.getBoundingClientRect()
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width))
  }
  function start(which: typeof drag) {
    drag = which
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', end)
  }
  function move(e: PointerEvent) {
    if (drag === 'tx') {
      const f = pos(e, tt)
      onasof(f > 0.985 ? null : txFrom + f * txSpan)
    } else if (drag === 'valid-from') {
      const f = pos(e, vt)
      const from = vFrom + f * vSpan
      onwindow(Math.min(from, (query.window?.to ?? now) - 60_000), query.window?.to ?? now)
    } else if (drag === 'valid-to') {
      const f = pos(e, vt)
      const to = vFrom + f * vSpan
      onwindow(query.window?.from ?? now - 86_400_000, Math.max(to, (query.window?.from ?? 0) + 60_000))
    }
  }
  function end() {
    drag = null
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', end)
  }
  function returnToNow() { onasof(null); sfx('click', { volume: 0.25 }) }

  const stamp = (ms: number) => new Date(ms).toLocaleString(undefined,
    { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
</script>

<footer class="band">
  <div class="col">
    <span class="tk caps">WHEN IT HAPPENED <Explain term="valid time" bare /></span>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="track valid" bind:this={vt}>
      <span class="brush" style={`left:${bFrom}%; width:${Math.max(1, bTo - bFrom)}%`}></span>
      <button class="grip" style={`left:${bFrom}%`} onpointerdown={() => start('valid-from')}
        aria-label="window start"></button>
      <button class="grip" style={`left:${bTo}%`} onpointerdown={() => start('valid-to')}
        aria-label="window end"></button>
    </div>
    <span class="stampsm caps">
      {stamp(query.window?.from ?? now)} → {stamp(query.window?.to ?? now)}
    </span>
  </div>

  <div class="wins">
    {#each WINDOWS as [label, ms]}
      <button class="win caps" onclick={() => onwindow(now - ms, now)}>{label}</button>
    {/each}
  </div>

  <div class="col tx">
    <span class="tk caps amb">WHAT WE KNEW BACK THEN <Explain term="transaction time" bare /></span>
    <div class="track belief" bind:this={tt}>
      <span class="dots"></span>
      <span class="past" style={`width:${txPct}%`}></span>
      <button class="grip amb" style={`left:${txPct}%`} onpointerdown={() => start('tx')}
        aria-label="belief time"></button>
      <span class="livecap caps">LIVE</span>
    </div>
    <span class="stampsm caps">
      {#if asOf === null}
        <span class="dim">NOW · EVERYTHING THE SYSTEM CURRENTLY BELIEVES</span>
      {:else}
        <span class="amb">AS BELIEVED ON {stamp(asOf)}</span>
        {#if delta}<span class="delta"> · {delta} FACT{delta === 1 ? '' : 'S'} DIFFER FROM NOW</span>{/if}
      {/if}
    </span>
  </div>

  <button class="ret caps" onclick={returnToNow} disabled={asOf === null}>⏎ RETURN TO NOW</button>
</footer>

<style>
  .band { display: grid; grid-template-columns: 1fr auto 1fr auto; align-items: center; gap: 18px;
    padding: 9px 18px; border-top: 1px solid var(--hairline); background: #04070a; flex: 0 0 auto; }
  .col { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .tk { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.18em; }
  .tk.amb { color: color-mix(in srgb, var(--amber) 70%, transparent); }
  .track { position: relative; height: 14px; background: rgba(236,236,236,0.05);
    border: 1px solid var(--hairline); cursor: crosshair; }
  .track.belief { height: 9px; background: none; border: none; border-bottom: 1px dotted var(--ink-ghost); }
  .brush { position: absolute; top: 0; bottom: 0; background: rgba(56,208,227,0.16);
    border-left: 1px solid var(--cyan); border-right: 1px solid var(--cyan); }
  .past { position: absolute; left: 0; top: 3px; height: 2px; background: color-mix(in srgb, var(--amber) 55%, transparent); }
  .dots { position: absolute; inset: 0; }
  .grip { position: absolute; top: -2px; width: 9px; height: 18px; margin-left: -4px; padding: 0;
    background: #04070a; border: 1px solid var(--cyan); cursor: ew-resize; touch-action: none; }
  .grip.amb { border-color: var(--amber); top: -4px; height: 16px; }
  .livecap { position: absolute; right: 2px; top: -11px; font-size: 6px; color: var(--ink-ghost);
    letter-spacing: 0.14em; }
  .stampsm { font-size: 7px; color: var(--ink-dim); letter-spacing: 0.1em; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
  .dim { color: var(--ink-ghost); } .amb { color: var(--amber); }
  .delta { color: var(--amber); }
  .wins { display: flex; gap: 3px; }
  .win { padding: 3px 6px; border: 1px solid var(--hairline); background: none; color: var(--ink-ghost);
    font-size: 7px; letter-spacing: 0.12em; cursor: crosshair; }
  .win:hover { color: var(--cyan); border-color: var(--cyan); }
  .ret { padding: 7px 12px; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim);
    font-size: 8px; letter-spacing: 0.14em; cursor: crosshair; white-space: nowrap; }
  .ret:hover:not(:disabled) { border-color: var(--amber); color: var(--amber); }
  .ret:disabled { opacity: 0.35; }
</style>
