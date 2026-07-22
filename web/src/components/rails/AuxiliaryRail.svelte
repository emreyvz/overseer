<script lang="ts">
  import { metrics, system } from '../../lib/stores'
  import { LEX } from '../../lib/lexicon'

  let rows = $derived([
    ...$metrics,
    { label: 'CPU', value: `%${Math.round($system.cpu)}` },
    { label: 'RAM', value: `%${Math.round($system.ram)}` },
    { label: 'GPU', value: $system.gpu == null ? 'YOK' : `%${Math.round($system.gpu)}` },
    { label: 'STORAGE', value: `${$system.storageGB.toFixed(2)} GB` },
    { label: 'REC', value: $system.recActive ? 'EVENT ●' : 'OFF' },
  ])
</script>

<div class="rail panel">
  <div class="head caps">{LEX.auxiliary}</div>
  <div class="list caps">
    {#each rows as m}
      <div class="row"><span class="k">{m.label}</span><span class="v">{m.value || '–'}</span></div>
    {/each}
  </div>
</div>

<style>
  .rail { position: absolute; right: 20px; bottom: 64px; width: 300px; max-height: 42vh; z-index: var(--z-panel);
    display: flex; flex-direction: column; padding: 8px; overflow: hidden; }
  .head { font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink); margin-bottom: 6px; }
  .list { overflow-y: auto; }
  .row { display: flex; justify-content: space-between; font-size: var(--fs-micro); line-height: 1.7; }
  .k { color: var(--ink-dim); }
  .v { color: var(--ink); }
</style>
