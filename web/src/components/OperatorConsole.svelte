<script lang="ts">
  // The AI Operator — a central, on-brand command surface. Speak or type a command in Turkish or
  // English; it is planned into a chain of system actions and executed while the screen border
  // lights up. A pulsing scarlet orb shows it is listening (kin to the boot "BEGIN" pip), the live
  // transcript streams in, and every executed step is written to the ledger. Spoken replies come
  // back through TTS. Settings (language, mic/output device, voice on/off) live behind the gear.
  import { onMount, onDestroy, tick } from 'svelte'
  import { get } from 'svelte/store'
  import { operatorOpen, aiOpen } from '../lib/stores'
  import { operate, operatorLog, operatorBusy, type LogEntry } from '../lib/operator'
  import {
    startSTT, stopSTT, speak, stopSpeaking, sttSupported, ttsSupported, loadPrefs, savePrefs,
    listAudioDevices, type Lang, type AudioDev,
  } from '../lib/speech'
  import { sfx } from '../lib/audio'

  let prefs = $state(loadPrefs())
  let listening = $state(false)
  let interim = $state('')
  let input = $state('')
  let settings = $state(false)
  let inputs = $state<AudioDev[]>([])
  let outputs = $state<AudioDev[]>([])
  let box = $state<HTMLDivElement>()
  let field = $state<HTMLInputElement>()

  const log = $derived($operatorLog)
  const busy = $derived($operatorBusy)

  async function scroll() { await tick(); if (box) box.scrollTop = box.scrollHeight }
  $effect(() => { void log.length; scroll() })

  onMount(() => { field?.focus() })
  onDestroy(() => { stopSTT(); stopSpeaking() })

  async function run(text: string) {
    const cmd = text.trim()
    if (!cmd) return
    input = ''; interim = ''
    const plan = await operate(cmd)
    if (plan?.say) speakIf(plan.say)
    else if (plan?.ask) speakIf(plan.ask)
    await scroll()
  }

  function speakIf(t: string) { if (prefs.tts) speak(t, prefs.lang) }

  function toggleMic() {
    if (listening) { stopSTT(); listening = false; return }
    if (!sttSupported()) { sfx('error'); return }
    stopSpeaking()
    sfx('click', { volume: 0.3 })
    const ok = startSTT(prefs.lang, {
      interim: (t) => (interim = t),
      final: (t) => { interim = ''; run(t) },
      end: () => (listening = false),
      error: () => { listening = false; sfx('error') },
    })
    listening = ok
  }

  function setLang(l: Lang) { prefs.lang = l; savePrefs({ lang: l }) }
  function toggleTts() { prefs.tts = !prefs.tts; savePrefs({ tts: prefs.tts }); if (!prefs.tts) stopSpeaking() }

  async function openSettings() {
    settings = !settings
    if (settings && !inputs.length) { const d = await listAudioDevices(); inputs = d.inputs; outputs = d.outputs }
  }
  function pickMic(e: Event) { const id = (e.target as HTMLSelectElement).value; prefs.micId = id; savePrefs({ micId: id }) }
  function pickOut(e: Event) { const id = (e.target as HTMLSelectElement).value; prefs.outId = id; savePrefs({ outId: id }) }

  function close() { stopSTT(); stopSpeaking(); operatorOpen.set(false) }
  // Enter runs the typed command; Escape is owned by App (which closes the operator first),
  // and unmount cleanup (stopSTT/stopSpeaking) runs in onDestroy.
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && document.activeElement === field) run(input)
  }

  const kindCls = (k: LogEntry['kind']) => k
</script>

<svelte:window onkeydown={onKey} />

<div class="scrim" onclick={close} role="presentation"></div>
<section class="op" role="dialog" aria-label="AI Operator">
  <header>
    <div class="brand caps"><span class="dot"></span>AI OPERATOR</div>
    <div class="lang caps">
      <button class:on={prefs.lang === 'tr'} onclick={() => setLang('tr')}>TR</button>
      <button class:on={prefs.lang === 'en'} onclick={() => setLang('en')}>EN</button>
    </div>
    <button class="gear" onclick={openSettings} aria-label="Voice settings" title="Voice settings">⚙</button>
    <button class="gear" onclick={() => { aiOpen.set(true); }} title="Open the assistant (chat / provider)">CHAT</button>
    <button class="x caps" onclick={close}>ESC</button>
  </header>

  {#if settings}
    <div class="settings">
      <label>MIC
        <select onchange={pickMic} value={prefs.micId ?? ''}>
          <option value="">System default</option>
          {#each inputs as d}<option value={d.id}>{d.label}</option>{/each}
        </select>
      </label>
      <label>OUTPUT
        <select onchange={pickOut} value={prefs.outId ?? ''}>
          <option value="">System default</option>
          {#each outputs as d}<option value={d.id}>{d.label}</option>{/each}
        </select>
      </label>
      <label class="chk"><input type="checkbox" checked={prefs.tts} onchange={toggleTts} /> SPEAK REPLIES</label>
      <p class="note">Voice uses the system default device; the picker records your preference. Turkish and English are supported, including the odd English word inside a Turkish sentence.</p>
    </div>
  {/if}

  <div class="ledger" bind:this={box}>
    {#if log.length === 0}
      <div class="hint caps">
        SPEAK OR TYPE A COMMAND
        <ul>
          <li>"store kamerasına geç"</li>
          <li>"forensic ekranını aç"</li>
          <li>"kırmızı ceket ara"</li>
          <li>"roster'ı aç ve kırmızılı kişileri bul"</li>
          <li>"silah görürsen alarm kuralı oluştur"</li>
        </ul>
      </div>
    {:else}
      {#each log as e}
        <div class="entry {kindCls(e.kind)}">
          <span class="mk">{e.kind === 'say' ? '❯' : e.kind === 'ask' ? '?' : e.kind === 'error' ? '×' : '▸'}</span>
          <span class="tx">{e.text}</span>
        </div>
      {/each}
    {/if}
  </div>

  <div class="prompt {listening ? 'live' : ''}">
    <button class="mic {listening ? 'on' : ''}" onclick={toggleMic} disabled={!sttSupported()}
            title={sttSupported() ? 'Hold a command' : 'Voice not supported in this browser'} aria-label="Microphone">
      <span class="orb"></span>
    </button>
    <input bind:this={field} bind:value={input} placeholder={listening ? (interim || 'listening…') : 'command…'}
           class:interim={listening && interim} spellcheck="false" />
    <button class="go caps" onclick={() => run(input)} disabled={busy || !input.trim()}>RUN</button>
  </div>
</section>

<style>
  .scrim { position: fixed; inset: 0; z-index: 380; background: rgba(4, 6, 8, 0.55); backdrop-filter: blur(2px); }
  .op { position: fixed; z-index: 381; left: 50%; top: 12vh; transform: translateX(-50%);
    width: min(660px, 92vw); max-height: 74vh; display: flex; flex-direction: column;
    background: rgba(10, 12, 14, 0.97); border: 1px solid rgba(255,255,255,0.12);
    box-shadow: 0 24px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(225,6,0,0.06); color: var(--ink); }

  header { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.08); }
  .brand { font-size: 13px; letter-spacing: 0.24em; display: flex; align-items: center; gap: 9px; }
  .brand .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--scarlet); box-shadow: 0 0 8px var(--scarlet); }
  .lang { margin-left: auto; display: flex; gap: 4px; }
  .lang button { background: none; border: 1px solid rgba(255,255,255,0.14); color: var(--ink-dim);
    font: inherit; font-size: 10px; letter-spacing: 0.12em; padding: 3px 8px; cursor: pointer; }
  .lang button.on { color: var(--ink); border-color: var(--ink); }
  .gear, .x { background: none; border: 1px solid rgba(255,255,255,0.14); color: var(--ink-dim);
    font: inherit; font-size: 11px; padding: 3px 8px; cursor: pointer; letter-spacing: 0.1em; }
  .gear:hover { color: var(--ink); }
  .x:hover { color: var(--scarlet); border-color: var(--scarlet); }

  .settings { padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.08); display: grid; gap: 10px;
    grid-template-columns: 1fr 1fr; font-size: 11px; color: var(--ink-dim); letter-spacing: 0.1em; }
  .settings label { display: flex; flex-direction: column; gap: 5px; }
  .settings select { background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.16); color: var(--ink); font: inherit; font-size: 12px; padding: 5px 8px; }
  .settings .chk { flex-direction: row; align-items: center; gap: 8px; }
  .settings .note { grid-column: 1 / -1; margin: 0; color: var(--ink-dim); opacity: 0.7; font-size: 10.5px; letter-spacing: 0.04em; line-height: 1.5; text-transform: none; }

  .ledger { flex: 1; overflow-y: auto; padding: 14px 16px; min-height: 120px; }
  .hint { color: var(--ink-dim); font-size: 11px; letter-spacing: 0.18em; }
  .hint ul { margin: 12px 0 0; padding: 0; list-style: none; display: grid; gap: 6px; }
  .hint li { color: var(--ink-dim); opacity: 0.65; font-size: 12px; letter-spacing: 0.02em; text-transform: none; font-family: var(--font-mono); }
  .entry { display: flex; gap: 9px; padding: 5px 0; font-size: 13px; line-height: 1.45; }
  .entry .mk { flex: 0 0 auto; width: 12px; color: var(--ink-dim); }
  .entry.say { color: var(--ink); }
  .entry.say .mk { color: #1fa971; }
  .entry.step .tx { color: var(--ink-dim); }
  .entry.ask { color: #e0a02e; }
  .entry.error { color: var(--scarlet); }

  .prompt { display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-top: 1px solid rgba(255,255,255,0.08); }
  .prompt.live { box-shadow: inset 0 2px 0 -1px var(--scarlet); }
  .mic { width: 38px; height: 38px; border-radius: 50%; border: 1px solid rgba(255,255,255,0.2); background: rgba(0,0,0,0.3);
    display: flex; align-items: center; justify-content: center; cursor: pointer; flex: 0 0 auto; }
  .mic:disabled { opacity: 0.35; cursor: default; }
  .mic .orb { width: 12px; height: 12px; border-radius: 50%; background: var(--ink-dim); transition: background 0.2s; }
  .mic.on { border-color: var(--scarlet); }
  .mic.on .orb { background: var(--scarlet); box-shadow: 0 0 10px var(--scarlet); animation: op-listen 1s ease-in-out infinite; }
  @keyframes op-listen { 0%,100% { transform: scale(0.8); opacity: 0.7; } 50% { transform: scale(1.25); opacity: 1; } }
  .prompt input { flex: 1; background: rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.14); color: var(--ink);
    font: inherit; font-size: 14px; padding: 9px 12px; letter-spacing: 0.02em; }
  .prompt input.interim { color: var(--ink-dim); font-style: italic; }
  .prompt input:focus { outline: none; border-color: rgba(225,6,0,0.5); }
  .go { background: none; border: 1px solid var(--scarlet); color: var(--scarlet); font: inherit; font-size: 11px;
    letter-spacing: 0.16em; padding: 8px 14px; cursor: pointer; flex: 0 0 auto; }
  .go:hover:not(:disabled) { background: rgba(225,6,0,0.12); }
  .go:disabled { opacity: 0.35; cursor: default; border-color: var(--ink-dim); color: var(--ink-dim); }
  @media (prefers-reduced-motion: reduce) { .mic.on .orb { animation: none; } }
</style>
