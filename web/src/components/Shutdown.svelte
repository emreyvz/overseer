<script lang="ts">
  // Exit animation (item 16): mirrors boot in reverse, then quits.
  import { onMount, onDestroy } from 'svelte'
  import { quitApp } from '../lib/system'
  import { sfx, keyTick, stopAmbience } from '../lib/audio'
  import { triggerGlitch } from '../lib/stores'

  let phase = $state(0) // 0 terminating · 1 glitch/offline · 2 collapse
  let line = $state(0)
  const LINES = ['TERMINATING PRIMARY OPERATIONS_', 'REVOKING FEED ACCESS_', 'PURGING SESSION DATA_']
  const timers: ReturnType<typeof setTimeout>[] = []
  const at = (ms: number, fn: () => void) => timers.push(setTimeout(fn, ms))

  onMount(() => {
    stopAmbience(); sfx('sonar')
    at(300, () => { line = 1; keyTick() })
    at(750, () => { line = 2; keyTick() })
    at(1150, () => { line = 3; keyTick() })
    at(1500, () => { phase = 1; sfx('glitch'); triggerGlitch(220) })
    at(2200, () => { phase = 2; sfx('whoosh') })
    at(3000, () => quitApp())
  })
  onDestroy(() => timers.forEach(clearTimeout))
</script>

<div class="sd" class:collapse={phase === 2}>
  {#if phase < 2}
    <div class="lines">
      {#each LINES as l, i}
        {#if line > i}<div class="ln" class:cur={i === line - 1 && line < 3}>{l}</div>{/if}
      {/each}
    </div>
    {#if phase >= 1}<div class="offline caps">SYSTEM OFFLINE</div>{/if}
  {/if}
  <div class="scanbar"></div>
</div>

<style>
  .sd { position: fixed; inset: 0; z-index: calc(var(--z-cmd) + 5); background: var(--void); overflow: hidden;
    display: flex; align-items: center; justify-content: center; }
  .lines { position: absolute; left: 6vw; bottom: 14vh; font-family: var(--font-type); font-size: 15px;
    letter-spacing: var(--tracking); text-transform: uppercase; color: var(--ink); }
  .ln { margin: 4px 0; }
  .cur::after { content: '\2588'; margin-left: 3px; animation: blink 0.8s steps(2) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .offline { font-family: var(--font-display); font-weight: 700; font-size: clamp(28px, 5vw, 56px);
    letter-spacing: var(--tracking-wide); color: var(--scarlet); text-shadow: 0 0 24px var(--scarlet-glow);
    animation: flick 0.5s steps(3) both; }
  @keyframes flick { 0%{opacity:0} 40%{opacity:1} 55%{opacity:0.3} 100%{opacity:1} }
  /* collapse to a single scanline, then black */
  .scanbar { position: absolute; left: 0; right: 0; top: 50%; height: 2px; background: var(--ink); opacity: 0; }
  .sd.collapse { background: #000; }
  .sd.collapse .scanbar { opacity: 1; animation: collapse 700ms var(--ease) forwards; }
  @keyframes collapse { 0% { transform: scaleY(140); opacity: 0.9; } 70% { transform: scaleY(1); opacity: 1; } 100% { transform: scaleX(0); opacity: 0; } }
</style>
