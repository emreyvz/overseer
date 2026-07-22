<script lang="ts">
  // Bottom-right anonymized tracklet-id decor column (brief §4.5 · OSINT §15).
  import { detections } from '../../lib/stores'
  const FALLBACK = ['TK_009.557-1', 'TK_001.193-10', 'TK_099.570-70']
</script>
<div class="ids caps" aria-hidden="true">
  {#each ($detections.length ? $detections.slice(0, 3).map((d) => d.id) : FALLBACK) as id}
    <div>{id}</div>
  {/each}
  <div class="sys">CTL_CODE 423</div>
  <div class="sys">[LINK]</div>
  <div class="sys">SYS.ONLINE +0092.32-8</div>
</div>
<style>
  .ids { position: absolute; right: 26px; bottom: 64px; z-index: var(--z-overlay);
    text-align: right; font-size: var(--fs-micro); letter-spacing: 0.08em; color: var(--ink-ghost); line-height: 1.7; pointer-events: none; }
  .sys { color: var(--ink-ghost); }
</style>
