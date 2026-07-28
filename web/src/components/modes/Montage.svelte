<script lang="ts">
  // Synchronized multi-camera live view (feature 9). Click a feed to observe it.
  import { cameras, activeCam, mode, alerts, triggerGlitch } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { sendCommand } from '../../lib/ws'
  import { SIM } from '../../lib/sim'
  import type { Camera } from '../../lib/types'
  import LiveThumb from '../LiveThumb.svelte'

  let crit = $derived($alerts.find((a) => a.severity === 'critical' && !a.ack))
  function observe(c: Camera) {
    sfx('glitch'); triggerGlitch(180)
    activeCam.set(c.id)
    if (!SIM) sendCommand(`connect:${c.name}`)
    mode.set('pov')
  }
</script>

<section class="montage">
  <div class="hdr caps"><span class="hot">▽ ///</span> LIVE WALL · {$cameras.length} FEEDS <span class="hint">ALL FEEDS AT ONCE &nbsp;·&nbsp; CLICK · OBSERVE &nbsp;·&nbsp; ESC · POV</span></div>

  {#if crit}<div class="banner caps">ACTIVE THREAT · {crit.type}</div>{/if}

  <div class="grid">
    {#each $cameras as c (c.id)}
      <button class="cell" class:active={c.id === $activeCam} onclick={() => observe(c)}>
        <div class="feed"><LiveThumb id={c.id} fps={4} download={c.download} /><span class="livedot caps">● LIVE</span></div>
        <div class="meta caps"><span class="nm">/// {c.name}</span><span class="id">CAM {c.id}</span></div>
      </button>
    {/each}
    {#if $cameras.length === 0}<div class="empty caps">NO FEEDS</div>{/if}
  </div>
</section>

<style>
  .montage { position: absolute; inset: 0; z-index: var(--z-boot); background: #04070a; overflow: auto; padding: 22px 30px 30px; }
  .hdr { font-size: var(--fs-title); letter-spacing: var(--tracking); color: var(--scarlet); margin-bottom: 14px; }
  .hdr .hint { float: right; font-size: var(--fs-micro); color: var(--ink-ghost); }
  .banner { display: inline-block; margin-bottom: 14px; padding: 5px 16px; background: var(--scarlet); color: #fff; letter-spacing: var(--tracking); box-shadow: 0 0 16px var(--scarlet-glow); }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
  .cell { padding: 0; overflow: hidden; border: 1px solid var(--hairline); cursor: crosshair; background: #000; text-align: left; }
  .cell:hover { border-color: var(--cyan); box-shadow: 0 0 18px rgba(56,208,227,0.2); }
  .cell.active { border-color: var(--scarlet); box-shadow: 0 0 18px var(--scarlet-glow); }
  .feed { position: relative; aspect-ratio: 16/9; }
  .livedot { position: absolute; top: 6px; left: 6px; z-index: 2; font-size: 8px; color: var(--scarlet); letter-spacing: 0.1em; text-shadow: 0 0 4px #000; animation: livep 1.6s ease-in-out infinite; }
  @keyframes livep { 50% { opacity: 0.4; } }
  .meta { display: flex; justify-content: space-between; padding: 5px 8px; font-size: var(--fs-micro); }
  .meta .nm { color: var(--ink); } .meta .id { color: var(--ink-ghost); }
  .cell:hover .meta .nm { color: var(--cyan); }
  .empty { grid-column: 1/-1; text-align: center; color: var(--ink-ghost); font-size: var(--fs-title); padding: 40px; }
</style>
