<script lang="ts">
  import { commandOpen, mode, stage, pickerView, zoneEditor, alertRules, objectRegister, storageScreen, watchlistOpen, aiOpen, suggestionsOpen, spatialOpen, activeCam, shuttingDown, flashBanner, triggerGlitch, type Mode } from '../../lib/stores'
  import { sfx, keyTick } from '../../lib/audio'
  import { sendCommand } from '../../lib/ws'
  import { LEX } from '../../lib/lexicon'

  let value = $state('')
  let el = $state<HTMLInputElement | null>(null)
  let sel = $state(0)

  $effect(() => { if ($commandOpen) { value = ''; sel = 0; queueMicrotask(() => el?.focus()) } })

  const MODE_WORDS: Record<string, Mode> = {
    forensic: 'forensic', archive: 'archive', pov: 'pov', roster: 'roster', case: 'case', cases: 'case',
    dossiers: 'dossiers', dossier: 'dossiers',
  }

  interface Sug { v: string; d: string }
  const BASE: Sug[] = [
    { v: 'sources', d: 'SOURCE PICKER' },
    { v: 'map', d: 'CAMERA MAP' },
    { v: 'cases', d: 'INVESTIGATION CASES' },
    { v: 'forensic', d: 'FORENSIC SEARCH' },
    { v: 'watchlist', d: 'WATCHLIST · TARGETS' },
    { v: 'roster', d: 'ROSTER · PEOPLE & VEHICLES' },
    { v: 'dossiers', d: 'IDENTITY DOSSIERS · REPEAT VISITORS · GAIT · RECONSTRUCT' },
    { v: 'ai', d: 'AI ASSISTANT · ASK / SEARCH' },
    { v: 'suggestions', d: 'SMART SUGGESTIONS · RECOMMENDATIONS' },
    { v: 'spatial', d: 'SPATIAL 3D SCENE · DEPTH RECONSTRUCTION' },
    { v: 'archive', d: 'ARCHIVE' },
    { v: 'zones', d: 'DRAW ZONE / LINE' },
    { v: 'object', d: 'TRACK UNKNOWN OBJECT' },
    { v: 'alerts', d: 'ALERT RULES' },
    { v: 'record', d: 'TOGGLE RECORDING' },
    { v: 'snapshot', d: 'CAPTURE SNAPSHOT' },
    { v: 'storage', d: 'STORAGE / RECORDINGS' },
    { v: 'shutdown', d: 'SHUT DOWN SYSTEM' },
  ]

  let suggestions = $derived.by<Sug[]>(() => {
    const lc = value.toLowerCase().trim()
    if (lc === '') return BASE
    return BASE.filter((s) => s.v.toLowerCase().startsWith(lc))
  })
  $effect(() => { if (sel >= suggestions.length) sel = 0 })

  function close() { commandOpen.set(false) }
  function complete(s: Sug) { value = s.v; sel = 0; el?.focus() }

  function run(override?: string) {
    const raw = (override ?? value).trim()
    if (!raw) { close(); return }
    const cmd = raw.toLowerCase()
    const head = cmd.split(/\s+/)[0]

    if (head in MODE_WORDS) { triggerGlitch(160); sfx('whoosh'); stage.set('live'); mode.set(MODE_WORDS[head]); close(); return }
    if (head === 'sources' || head === 'cameras') { sfx('whoosh'); triggerGlitch(180); pickerView.set('grid'); stage.set('select'); close(); return }
    if (head === 'map') { sfx('whoosh'); triggerGlitch(180); pickerView.set('map'); stage.set('select'); close(); return }
    if (head === 'zones' || head === 'zone' || head === 'line') { sfx('click'); mode.set('pov'); zoneEditor.set(true); close(); return }
    if (head === 'object' || head === 'ooi') { sfx('click'); mode.set('pov'); objectRegister.set(true); close(); return }
    if (head === 'alerts' || head === 'alert') { sfx('click'); alertRules.set(true); close(); return }
    if (head === 'watchlist' || head === 'watch') { sfx('click'); watchlistOpen.set(true); close(); return }
    if (head === 'ai' || head === 'assistant' || head === 'asistan') { sfx('click'); aiOpen.set(true); close(); return }
    if (head === 'suggestions' || head === 'suggest' || head === 'tips') { sfx('click'); suggestionsOpen.set(true); close(); return }
    if (head === 'spatial' || head === '3d' || head === 'depth') { if ($activeCam) { sfx('click'); mode.set('pov'); spatialOpen.set($activeCam) } else { flashBanner('SELECT A CAMERA FIRST', false, 1400) } close(); return }
    if (head === 'storage') { sfx('click'); storageScreen.set(true); close(); return }
    if (head === 'record') { sfx('click'); flashBanner('RECORDING ON', false, 1200); sendCommand('record:toggle'); close(); return }
    if (head === 'snapshot') { sfx('shutter'); flashBanner('SNAPSHOT CAPTURED', false, 1000); sendCommand('snapshot'); close(); return }
    if (head === 'shutdown' || head === 'exit' || head === 'quit') { close(); shuttingDown.set(true); return }

    sfx('sonar'); flashBanner(LEX.scanning, false, 1400); stage.set('live'); mode.set('forensic'); sendCommand(`search:${raw}`); close()
  }

  function onKey(e: KeyboardEvent) {
    e.stopPropagation()
    if (e.key === 'Escape') { sfx('click'); close(); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = suggestions.length ? (sel + 1) % suggestions.length : 0; return }
    if (e.key === 'ArrowUp') { e.preventDefault(); sel = suggestions.length ? (sel - 1 + suggestions.length) % suggestions.length : 0; return }
    if (e.key === 'Tab') { e.preventDefault(); if (suggestions[sel]) complete(suggestions[sel]); return }
    // Enter runs the highlighted suggestion (or the raw text when none matches)
    if (e.key === 'Enter') { run(suggestions[sel]?.v); return }
    if (e.key.length === 1) keyTick()
  }
</script>

{#if $commandOpen}
  <button class="scrim" aria-label="close" onpointerdown={close}></button>
  <div class="wrap">
    {#if suggestions.length}
      <ul class="sugs caps">
        {#each suggestions as s, i}
          <li>
            <button class="sug" class:on={i === sel} onpointerdown={() => { complete(s); run() }} onmouseenter={() => (sel = i)}>
              <span class="sv">{s.v.trim().toUpperCase()}</span><span class="sd">{s.d}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
    <div class="bar">
      <span class="lead caps">{LEX.cmd}</span>
      <input bind:this={el} bind:value class="type" onkeydown={onKey}
        placeholder="ENTER TARGET_ · ↑↓ NAV · TAB COMPLETE" spellcheck="false" autocomplete="off" />
      <span class="hint caps">ENTER · RUN</span>
    </div>
  </div>
{/if}

<style>
  .scrim { position: fixed; inset: 0; z-index: var(--z-cmd); background: rgba(0,0,0,0.4); }
  .wrap { position: fixed; left: 50%; bottom: 22vh; transform: translateX(-50%); z-index: calc(var(--z-cmd) + 1); width: min(680px, 82vw); }
  .sugs { margin: 0 0 6px; background: #000; border: 1px solid var(--hairline); max-height: 40vh; overflow-y: auto; }
  .sug { display: flex; justify-content: space-between; align-items: center; width: 100%; padding: 7px 14px;
    font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink-dim); border-left: 2px solid transparent; }
  .sug.on { color: var(--ink); background: #14161a; border-left-color: var(--scarlet); }
  .sv { color: inherit; } .sd { font-size: var(--fs-micro); color: var(--ink-ghost); }
  .bar { display: flex; align-items: center; gap: 12px; background: #000; border: 1px solid var(--ink); padding: 12px 16px; box-shadow: 0 0 24px rgba(0,0,0,0.8); animation: rise 120ms var(--ease); }
  .lead { color: var(--scarlet); font-size: var(--fs-label); }
  input { flex: 1; background: transparent; border: none; outline: none; color: var(--ink);
    font-family: var(--font-type); font-size: var(--fs-banner); letter-spacing: var(--tracking); text-transform: uppercase; caret-color: var(--scarlet); }
  input::placeholder { color: var(--ink-ghost); letter-spacing: 0.08em; }
  .hint { font-size: var(--fs-micro); color: var(--ink-ghost); white-space: nowrap; }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
</style>
