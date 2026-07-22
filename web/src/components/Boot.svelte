<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { LEX } from '../lib/lexicon'
  import { sfx, startAmbience, keyTick } from '../lib/audio'
  import { triggerGlitch } from '../lib/stores'

  let { onbegin }: { onbegin: () => void } = $props()

  let phase = $state(1) // 1 code · 2 wake (+ begin button under OVERSEER)
  let line = $state(0)
  let showBegin = $state(false)
  let leaving = $state(false)
  let done = $state(false)
  const timers: ReturnType<typeof setTimeout>[] = []
  const at = (ms: number, fn: () => void) => timers.push(setTimeout(fn, ms))

  const CODE = Array.from({ length: 80 }, (_, i) => {
    const hex = () => Math.floor(Math.random() * 0xffff).toString(16).padStart(4, '0')
    const words = ['ext4_fill_super', 'kmalloc', 'sched_clock', 'tcp_v4_rcv', 'do_syscall_64',
      'vfs_read', 'mmap_region', 'irq_handler', 'crypto_aead', 'netlink_unicast', 'dev_queue_xmit', 'ksys_write']
    return `[${(i * 0.013).toFixed(6)}] ${words[i % words.length]}+0x${hex()}/0x${hex()} <${hex()}${hex()}>`
  })
  const LINES = [LEX.boot1, LEX.boot2, `${LEX.boot3} ▓▓▓▓▓▓▓ 100%`]
  // scattered "feed montage" tiles for the data-universe wake (feature 19)
  const rnd = (a: number, b: number) => a + Math.random() * (b - a)
  const TILES = Array.from({ length: 26 }, () => ({ x: rnd(3, 90), y: rnd(4, 90), w: rnd(4, 9), h: rnd(3, 6), d: rnd(0, 700) }))

  function burst(n: number) { for (let i = 0; i < n; i++) at(i * (60 + Math.random() * 90), keyTick) }

  function begin() {
    if (done || !showBegin) return
    done = true; leaving = true
    sfx('whoosh'); triggerGlitch(260)
    timers.forEach(clearTimeout)
    setTimeout(onbegin, 420)
  }

  onMount(() => {
    startAmbience()
    at(400, () => { line = 1; burst(3) })
    at(1050, () => { line = 2; burst(3) })
    at(1700, () => { line = 3; burst(4) })
    at(2200, () => { triggerGlitch(180); sfx('glitch'); phase = 2 })
    at(2800, () => sfx('sonar'))
    at(3300, () => { showBegin = true; sfx('click') })
  })
  onDestroy(() => timers.forEach(clearTimeout))

  function onKey(e: KeyboardEvent) {
    if (showBegin && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); begin() }
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="boot" class:paper={phase === 2} class:leaving>
  {#if phase === 1}
    <pre class="code">{CODE.join('\n')}</pre>
    <div class="lines">
      {#each LINES as l, i}
        {#if line > i}<div class="ln" class:cur={i === line - 1 && line < 3}>{l}</div>{/if}
      {/each}
    </div>
  {:else}
    <div class="wake">
      <div class="mbg" aria-hidden="true">
        {#each TILES as t}
          <span class="mtile" style={`left:${t.x}%;top:${t.y}%;width:${t.w}%;height:${t.h}%;animation-delay:${t.d}ms`}></span>
        {/each}
      </div>
      <div class="wordmark">OVERSEER</div>
      <div class="tri"></div>
      {#if showBegin}
        <button class="begin caps" onclick={begin}>
          <span class="pip"></span>BEGIN OBSERVATION<span class="arrow">▶</span>
        </button>
        <div class="sub caps">ENTER · BEGIN</div>
      {/if}
      <div class="repo">github.com/emreyvz/overseer</div>
    </div>
  {/if}
</div>

<style>
  .boot { position: fixed; inset: 0; z-index: var(--z-boot); background: var(--void); overflow: hidden;
    transition: opacity 400ms var(--ease), filter 400ms var(--ease); }
  .boot.paper { background: var(--paper); }
  .boot.leaving { opacity: 0; filter: blur(6px) contrast(1.4); }

  .code { position: absolute; inset: 0; margin: 0; padding: 4vh 6vw;
    font-family: var(--font-mono); font-size: 12px; line-height: 1.5;
    color: var(--ink-ghost); white-space: pre; opacity: 0.7; animation: scroll 2.3s linear forwards; }
  @keyframes scroll { from { transform: translateY(22%); } to { transform: translateY(-70%); } }

  .lines { position: absolute; left: 6vw; bottom: 12vh;
    font-family: var(--font-type); font-size: 15px; letter-spacing: var(--tracking);
    text-transform: uppercase; color: var(--ink); text-shadow: 0 0 8px rgba(236,236,236,0.15); }
  .ln { margin: 4px 0; }
  .cur::after { content: '\2588'; margin-left: 3px; animation: blink 0.8s steps(2) infinite; }
  @keyframes blink { 50% { opacity: 0; } }

  .wake { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0;
    text-align: center; background-image: repeating-linear-gradient(0deg, transparent 0 3px, var(--paper-line) 3px 4px); }
  .mbg { position: absolute; inset: 0; pointer-events: none; }
  .mtile { position: absolute; background: #b9b8b4;
    background-image: repeating-linear-gradient(0deg, rgba(0,0,0,0.1) 0 2px, transparent 2px 4px);
    border: 1px solid rgba(0,0,0,0.15); opacity: 0; animation: tilein 700ms var(--ease) both; }
  @keyframes tilein { from { opacity: 0; transform: scale(0.6) translateY(8px); filter: blur(4px); } to { opacity: 0.5; transform: none; filter: none; } }
  .wordmark { font-family: var(--font-display); font-weight: 700; font-size: clamp(40px, 8vw, 96px);
    letter-spacing: 0.2em; color: var(--paper-ink); border-bottom: 3px solid var(--paper-ink); padding-bottom: 10px;
    animation: reveal 900ms var(--ease) both; }
  .tri { width: 0; height: 0; margin: 18px auto 0; border-left: 12px solid transparent; border-right: 12px solid transparent;
    border-bottom: 20px solid var(--scarlet); animation: reveal 900ms 160ms var(--ease) both; }

  .begin { display: inline-flex; align-items: center; gap: 14px; margin-top: 40px; padding: 13px 34px;
    background: transparent; border: 1.5px solid var(--paper-ink); color: var(--paper-ink);
    font-family: var(--font-display); font-weight: 600; font-size: 18px; letter-spacing: var(--tracking-wide);
    animation: emerge 400ms var(--ease) both; }
  .begin:hover { background: var(--scarlet); border-color: var(--scarlet); color: #fff; box-shadow: 0 0 30px var(--scarlet-glow); }
  .begin .pip { width: 9px; height: 9px; background: var(--scarlet); }
  .begin:hover .pip { background: #fff; }
  .begin .arrow { font-size: 12px; color: var(--scarlet); }
  .begin:hover .arrow { color: #fff; }
  @keyframes emerge { from { opacity: 0; transform: translateY(10px) scale(0.96); } }
  @keyframes reveal { from { opacity: 0; filter: blur(6px); } to { opacity: 1; filter: none; } }
  .sub { margin-top: 12px; font-size: var(--fs-micro); color: rgba(10,10,10,0.4); letter-spacing: var(--tracking); }
  .repo { position: absolute; bottom: 34px; left: 0; right: 0; text-align: center;
    font-family: var(--font-mono); font-size: 14px; font-weight: 600; letter-spacing: 0.16em;
    color: rgba(10,10,10,0.72); }
  .repo::before { content: '⌂ '; color: var(--scarlet); opacity: 1; }
</style>
