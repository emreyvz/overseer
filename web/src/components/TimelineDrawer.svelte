<script lang="ts">
  import { timeline, timelineOpen } from '../lib/stores'
  import { LEX } from '../lib/lexicon'
  const hhmmss = (ts: number) => {
    const d = new Date(ts)
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')
  }
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Tab') { e.preventDefault(); timelineOpen.update((v) => !v) } }} />

<div class="drawer panel" class:open={$timelineOpen}>
  <div class="head caps">{LEX.timeline} <span class="hint">· TAB</span></div>
  <div class="strip">
    {#each $timeline.slice(0, 60) as e}
      <div class="ev caps">
        <span class="t">{hhmmss(e.ts)}</span>
        <span class="ty">{e.type}</span>
        <span class="lb">{e.label}{e.conf ? ` (${e.conf.toFixed(2)})` : ''}</span>
      </div>
    {/each}
    {#if $timeline.length === 0}<div class="empty caps">NO EVENTS</div>{/if}
  </div>
</div>

<style>
  .drawer { position: absolute; left: 60px; right: 60px; bottom: 44px; z-index: var(--z-panel);
    height: 0; overflow: hidden; padding: 0 10px; transition: height var(--t-snap) var(--ease); }
  .drawer.open { height: 128px; padding: 8px 10px; }
  .head { font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink); margin-bottom: 6px; }
  .hint { color: var(--ink-ghost); }
  .strip { display: flex; flex-direction: column; gap: 2px; overflow-y: auto; max-height: 84px; }
  .ev { display: grid; grid-template-columns: 70px 90px 1fr; gap: 8px; font-size: var(--fs-micro); color: var(--ink-dim); }
  .ev .t { color: var(--ink-ghost); }
  .ev .ty { color: var(--ink); }
  .empty { color: var(--ink-ghost); font-size: var(--fs-micro); }
</style>
