<script lang="ts">
  // Live object-of-interest tracking boxes over the POV.
  import { ooiTargets } from '../../lib/stores'
  const pc = (n: number) => `${(n * 100).toFixed(2)}%`
</script>

<div class="ooil">
  {#each $ooiTargets as o (o.id)}
    <div class="ooi" class:lost={o.lost}
      style={`left:${pc(o.bbox[0])};top:${pc(o.bbox[1])};width:${pc(o.bbox[2])};height:${pc(o.bbox[3])}`}>
      <span class="cn a"></span><span class="cn b"></span><span class="cn c"></span><span class="cn d"></span>
      <span class="lbl caps">◈ {o.name}{#if o.lost} · LOST{/if}</span>
    </div>
  {/each}
</div>

<style>
  .ooil { position: absolute; inset: 0; z-index: var(--z-overlay); pointer-events: none; }
  .ooi { position: absolute; transition: left 200ms linear, top 200ms linear, width 200ms linear, height 200ms linear; }
  .cn { position: absolute; width: 8px; height: 8px; border: 1.5px solid var(--cyan); box-shadow: 0 0 5px var(--cyan); }
  .a { top: -1px; left: -1px; border-right: 0; border-bottom: 0; }
  .b { top: -1px; right: -1px; border-left: 0; border-bottom: 0; }
  .c { bottom: -1px; left: -1px; border-right: 0; border-top: 0; }
  .d { bottom: -1px; right: -1px; border-left: 0; border-top: 0; }
  .ooi.lost .cn { border-color: var(--scarlet); box-shadow: none; }
  .lbl { position: absolute; top: -15px; left: 0; font-size: 9px; letter-spacing: 0.1em; color: var(--cyan); text-shadow: 0 0 4px #000; white-space: nowrap; }
  .ooi.lost .lbl { color: var(--scarlet); }
</style>
