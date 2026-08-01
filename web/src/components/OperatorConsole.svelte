<script lang="ts">
  // The AI Operator — the single AI surface (the old assistant is folded in here: chat, provider
  // settings and feature toggles all live in this one place). Speak or type a command or a
  // question in English or Turkish; it is planned into a chain of system actions and executed,
  // or answered. A status light shows exactly what it is doing (listening / thinking / acting /
  // answered / error). When it runs actions, the panel steps aside into a COMPANION dock on the
  // right so you can watch the screen it is driving, and you can expand it back any time.
  import { onMount, onDestroy, tick } from 'svelte'
  import { get } from 'svelte/store'
  import { operatorOpen, flashBanner } from '../lib/stores'
  import { operate, planNavigates, operatorLog, operatorBusy, type LogEntry } from '../lib/operator'
  import { aiStatus, refreshAiStatus, AI_FEATURES } from '../lib/ai'
  import { api } from '../lib/api'
  import {
    startRecording, stopRecording, recordingSupported, speak, stopSpeaking, loadPrefs, savePrefs,
    listAudioDevices, type Lang, type AudioDev,
  } from '../lib/speech'
  import { sfx } from '../lib/audio'

  let prefs = $state(loadPrefs())
  let view = $state<'full' | 'companion'>('full')
  let status = $state<'idle' | 'listening' | 'thinking' | 'acting' | 'ok' | 'error'>('idle')
  let listening = $state(false)
  let interim = $state('')
  let input = $state('')
  let panel = $state<'none' | 'settings'>('none')
  let inputs = $state<AudioDev[]>([])
  let outputs = $state<AudioDev[]>([])
  let box = $state<HTMLDivElement>()
  let field = $state<HTMLInputElement>()
  let statusTimer: ReturnType<typeof setTimeout> | undefined

  const log = $derived($operatorLog)
  const busy = $derived($operatorBusy)

  // ---- AI provider settings (folded in from the old assistant) --------------------------------
  const PRESETS: Record<string, { base: string; model: string; vision: string }> = {
    GLM: { base: 'https://api.z.ai/api/coding/paas/v4', model: 'glm-4.6', vision: 'glm-4.6v' },
    OPENAI: { base: 'https://api.openai.com/v1', model: 'gpt-4o-mini', vision: 'gpt-4o-mini' },
    CUSTOM: { base: '', model: '', vision: '' },
  }
  let cfg = $state({ provider: 'GLM', base_url: '', api_key: '', model: '', vision_model: '' })
  let featForm = $state<Record<string, boolean>>({})
  let saving = $state(false)
  let testMsg = $state<{ ok: boolean; detail: string } | null>(null)

  function loadCfg() {
    const s = get(aiStatus)
    cfg = {
      provider: (s.provider || 'glm').toUpperCase() in PRESETS ? (s.provider || 'glm').toUpperCase() : 'CUSTOM',
      base_url: s.base ?? '', api_key: '', model: s.model ?? '', vision_model: s.vision_model ?? '',
    }
    featForm = Object.fromEntries(AI_FEATURES.map((f) => [f.key, s.features?.[f.key] !== false]))
  }
  function applyPreset(p: string) {
    cfg.provider = p
    if (PRESETS[p] && p !== 'CUSTOM') { cfg.base_url = PRESETS[p].base; cfg.model = PRESETS[p].model; cfg.vision_model = PRESETS[p].vision }
    sfx('click', { volume: 0.25 })
  }
  async function testCfg() {
    saving = true; testMsg = null
    try { testMsg = await api.aiTest({ provider: cfg.provider.toLowerCase(), base_url: cfg.base_url, api_key: cfg.api_key, model: cfg.model }) }
    catch { testMsg = { ok: false, detail: 'TEST FAILED' } }
    saving = false; sfx(testMsg?.ok ? 'ping' : 'error')
  }
  async function saveCfg() {
    saving = true
    try {
      const s = await api.aiConfig({ provider: cfg.provider.toLowerCase(), base_url: cfg.base_url, api_key: cfg.api_key,
        model: cfg.model, vision_model: cfg.vision_model, features: { ...featForm } })
      aiStatus.set(s); sfx('ping')
      flashBanner(s.enabled ? `OPERATOR ONLINE · ${s.model}` : 'SAVED · KEY REQUIRED', !s.enabled, 1600)
    } catch { flashBanner('SAVE FAILED', true, 1400); sfx('error') }
    saving = false
  }

  async function scroll() { await tick(); if (box) box.scrollTop = box.scrollHeight }
  $effect(() => { void log.length; scroll() })

  onMount(async () => { field?.focus(); await refreshAiStatus(); loadCfg() })
  onDestroy(() => { stopSTT(); stopSpeaking(); clearTimeout(statusTimer) })

  function flash(s: typeof status, ms = 1600) {
    status = s
    clearTimeout(statusTimer)
    statusTimer = setTimeout(() => { if (status === s) status = 'idle' }, ms)
  }

  async function run(text: string) {
    const cmd = text.trim()
    if (!cmd || busy) return
    input = ''; interim = ''; panel = 'none'
    status = 'thinking'
    const plan = await operate(cmd)
    if (plan && planNavigates(plan)) {
      status = 'acting'
      // step aside into the companion dock so the operator can watch the screen it drove
      setTimeout(() => { view = 'companion'; flash('ok', 1400) }, 350)
    } else {
      flash(plan?.disabled ? 'error' : 'ok')   // answers/questions keep the full panel
    }
    if (plan?.say) speakIf(plan.say)
    else if (plan?.ask) speakIf(plan.ask)
    await scroll()
  }

  function speakIf(t: string) { if (prefs.tts) speak(t, prefs.lang) }

  // Offline voice: click to record, click again to stop -> transcribe on the backend -> run it.
  async function toggleMic() {
    if (listening) {
      listening = false; status = 'thinking'
      const blob = await stopRecording()
      if (!blob) { flash('error'); return }
      try {
        const { text, disabled } = await api.stt(blob, prefs.lang)
        if (disabled) { flashBanner('OFFLINE VOICE MODEL NOT INSTALLED', true, 2200); flash('error'); return }
        if (text && text.trim()) run(text)
        else { flashBanner('DIDN\'T CATCH THAT', true, 1400); flash('ok') }
      } catch { flashBanner('VOICE TRANSCRIBE FAILED', true, 1800); flash('error') }
      return
    }
    if (!recordingSupported()) { flashBanner('NO MICROPHONE AVAILABLE', true, 1800); sfx('error'); flash('error'); return }
    stopSpeaking(); sfx('click', { volume: 0.3 })
    const ok = await startRecording(prefs.micId)
    listening = ok; status = ok ? 'listening' : 'error'
    if (!ok) { flashBanner('MICROPHONE PERMISSION DENIED', true, 1800); sfx('error') }
  }

  function setLang(l: Lang) { prefs.lang = l; savePrefs({ lang: l }) }
  function toggleTts() { prefs.tts = !prefs.tts; savePrefs({ tts: prefs.tts }); if (!prefs.tts) stopSpeaking() }

  async function openSettings() {
    if (panel === 'settings') { panel = 'none'; return }
    panel = 'settings'; testMsg = null; loadCfg()
    if (!inputs.length) { const d = await listAudioDevices(); inputs = d.inputs; outputs = d.outputs }
  }
  function pickMic(e: Event) { const id = (e.target as HTMLSelectElement).value; prefs.micId = id; savePrefs({ micId: id }) }
  function pickOut(e: Event) { const id = (e.target as HTMLSelectElement).value; prefs.outId = id; savePrefs({ outId: id }) }

  function expand() { view = 'full'; panel = 'none'; sfx('click', { volume: 0.2 }); tick().then(() => field?.focus()) }
  function close() { stopSTT(); stopSpeaking(); operatorOpen.set(false) }

  const STATUS_LABEL: Record<typeof status, string> = {
    idle: 'READY', listening: 'LISTENING', thinking: 'THINKING', acting: 'ACTING', ok: 'DONE', error: 'ERROR',
  }
</script>

{#if view === 'full'}
  <div class="scrim" onclick={() => (view = 'companion')} role="presentation"></div>
{/if}

<section class="op {view}" role="dialog" aria-label="AI Operator">
  <header>
    <div class="brand caps">
      <span class="dot {status}"></span>AI OPERATOR
      <span class="stat caps {status}">{STATUS_LABEL[status]}</span>
    </div>
    {#if view === 'full'}
      <div class="lang caps">
        <button class:on={prefs.lang === 'en'} onclick={() => setLang('en')}>EN</button>
        <button class:on={prefs.lang === 'tr'} onclick={() => setLang('tr')}>TR</button>
      </div>
      <button class="gear" class:on={panel === 'settings'} onclick={openSettings} aria-label="Settings" title="Settings">⚙</button>
      <button class="x caps" onclick={close}>ESC</button>
    {:else}
      <button class="grow" onclick={expand} title="Expand" aria-label="Expand">⤢</button>
      <button class="x caps" onclick={close}>✕</button>
    {/if}
  </header>

  {#if view === 'full' && panel === 'settings'}
    <div class="settings">
      <div class="sgrp caps">VOICE</div>
      <label class="row">MIC
        <select onchange={pickMic} value={prefs.micId ?? ''}>
          <option value="">System default</option>
          {#each inputs as d}<option value={d.id}>{d.label}</option>{/each}
        </select>
      </label>
      <label class="row">OUTPUT
        <select onchange={pickOut} value={prefs.outId ?? ''}>
          <option value="">System default</option>
          {#each outputs as d}<option value={d.id}>{d.label}</option>{/each}
        </select>
      </label>
      <label class="chk"><input type="checkbox" checked={prefs.tts} onchange={toggleTts} /> SPEAK REPLIES</label>

      <div class="sgrp caps">AI PROVIDER</div>
      <div class="presets caps">
        {#each Object.keys(PRESETS) as p}<button class:on={cfg.provider === p} onclick={() => applyPreset(p)}>{p}</button>{/each}
      </div>
      <label class="row">BASE URL <input bind:value={cfg.base_url} placeholder="https://…" spellcheck="false" /></label>
      <label class="row">API KEY <input bind:value={cfg.api_key} type="password" placeholder="•••• (leave blank to keep)" spellcheck="false" /></label>
      <label class="row">MODEL <input bind:value={cfg.model} spellcheck="false" /></label>
      <label class="row">VISION MODEL <input bind:value={cfg.vision_model} spellcheck="false" /></label>
      <div class="feats">
        {#each AI_FEATURES as f}
          <label class="chk sm" title={f.desc}><input type="checkbox" bind:checked={featForm[f.key]} /> {f.label}</label>
        {/each}
      </div>
      {#if testMsg}<div class="test caps {testMsg.ok ? 'ok' : 'bad'}">{testMsg.ok ? '✓' : '✕'} {testMsg.detail}</div>{/if}
      <div class="sbtns">
        <button class="sbtn" disabled={saving} onclick={testCfg}>{saving ? '…' : 'TEST'}</button>
        <button class="sbtn go" disabled={saving} onclick={saveCfg}>SAVE</button>
      </div>
    </div>
  {:else}
    <div class="ledger" bind:this={box}>
      {#if log.length === 0}
        <div class="hint caps">
          SPEAK OR TYPE A COMMAND OR QUESTION
          <ul>
            <li>"switch to the street camera"</li>
            <li>"open forensic and search for a grey car"</li>
            <li>"how many people are on the hotel camera?"</li>
            <li>"open the roster and find red-flagged people"</li>
            <li>"alarm if you see a weapon"</li>
          </ul>
        </div>
      {:else}
        {#each log as e}
          <div class="entry {e.kind}">
            <span class="mk">{e.kind === 'you' ? '›' : e.kind === 'say' ? '❯' : e.kind === 'ask' ? '?' : e.kind === 'error' ? '×' : '▸'}</span>
            <span class="tx">{e.text}</span>
          </div>
        {/each}
      {/if}
    </div>

    <div class="prompt {listening ? 'live' : ''}">
      <button class="mic {listening ? 'on' : ''}" onclick={toggleMic} title="Voice command" aria-label="Microphone"><span class="orb"></span></button>
      <input bind:this={field} bind:value={input} onkeydown={(e) => e.key === 'Enter' && run(input)}
             placeholder={listening ? (interim || 'listening…') : (view === 'companion' ? 'command…' : 'command or question…')}
             class:interim={listening && interim} spellcheck="false" />
      <button class="go caps" onclick={() => run(input)} disabled={busy || !input.trim()}>RUN</button>
    </div>
  {/if}
</section>

<style>
  .scrim { position: fixed; inset: 0; z-index: 380; background: rgba(4, 6, 8, 0.5); backdrop-filter: blur(2px); animation: fade 200ms both; }
  @keyframes fade { from { opacity: 0; } }

  .op { position: fixed; z-index: 381; display: flex; flex-direction: column;
    background: rgba(10, 12, 14, 0.97); border: 1px solid rgba(255,255,255,0.12); color: var(--ink);
    box-shadow: 0 24px 80px rgba(0,0,0,0.6); }
  .op.full { left: 50%; top: 12vh; transform: translateX(-50%); width: min(680px, 92vw); max-height: 76vh;
    animation: popIn 260ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  .op.companion { right: 18px; top: 50%; transform: translateY(-50%); width: 340px; max-height: 62vh;
    border-color: rgba(47,169,113,0.4); box-shadow: 0 18px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(47,169,113,0.15);
    animation: dockIn 380ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes popIn { from { opacity: 0; transform: translateX(-50%) translateY(-10px) scale(0.98); } }
  @keyframes dockIn { from { opacity: 0; transform: translateY(-50%) translateX(60px) scale(0.9); } }

  header { display: flex; align-items: center; gap: 10px; padding: 11px 14px; border-bottom: 1px solid rgba(255,255,255,0.08); }
  .brand { font-size: 12px; letter-spacing: 0.22em; display: flex; align-items: center; gap: 8px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink-dim); transition: background 0.2s; flex: 0 0 auto; }
  .dot.idle { background: #2fa971; }
  .dot.listening { background: var(--scarlet); box-shadow: 0 0 9px var(--scarlet); animation: blink 1s ease-in-out infinite; }
  .dot.thinking, .dot.acting { background: #e0a02e; box-shadow: 0 0 8px #e0a02e; animation: blink 0.8s ease-in-out infinite; }
  .dot.ok { background: #2fa971; box-shadow: 0 0 8px #2fa971; }
  .dot.error { background: var(--scarlet); box-shadow: 0 0 8px var(--scarlet); }
  .stat { font-size: 8px; letter-spacing: 0.16em; color: var(--ink-dim); }
  .stat.listening, .stat.error { color: var(--scarlet); }
  .stat.thinking, .stat.acting { color: #e0a02e; }
  .stat.ok, .stat.idle { color: #2fa971; }
  @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }

  .lang { margin-left: auto; display: flex; gap: 4px; }
  .lang button { background: none; border: 1px solid rgba(255,255,255,0.14); color: var(--ink-dim); font: inherit; font-size: 10px; letter-spacing: 0.12em; padding: 3px 8px; cursor: pointer; }
  .lang button.on { color: var(--ink); border-color: var(--ink); }
  .gear, .x, .grow { background: none; border: 1px solid rgba(255,255,255,0.14); color: var(--ink-dim); font: inherit; font-size: 11px; padding: 3px 8px; cursor: pointer; letter-spacing: 0.1em; }
  .grow { margin-left: auto; }
  .gear.on { color: var(--ink); border-color: var(--ink); }
  .gear:hover, .grow:hover { color: var(--ink); } .x:hover { color: var(--scarlet); border-color: var(--scarlet); }

  /* settings: everything stacked full-width so nothing overflows */
  .settings { padding: 12px 14px 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 9px; font-size: 11px; color: var(--ink-dim); letter-spacing: 0.06em; }
  .sgrp { font-size: 8px; letter-spacing: 0.2em; color: var(--ink-dim); opacity: 0.7; margin-top: 6px; border-bottom: 1px solid rgba(255,255,255,0.07); padding-bottom: 5px; }
  .settings .row { display: flex; flex-direction: column; gap: 4px; }
  .settings .row select, .settings .row input { width: 100%; box-sizing: border-box; background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.16); color: var(--ink); font: inherit; font-size: 12px; padding: 6px 8px; }
  .chk { display: flex; flex-direction: row; align-items: center; gap: 8px; cursor: pointer; justify-content: flex-start; }
  .chk input { flex: 0 0 auto; width: 14px; height: 14px; }
  .chk.sm { font-size: 10px; }
  .presets { display: flex; gap: 6px; }
  .presets button { flex: 1; background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.16); color: var(--ink-dim); font: inherit; font-size: 10px; letter-spacing: 0.12em; padding: 5px; cursor: pointer; }
  .presets button.on { color: var(--ink); border-color: var(--ink); }
  .feats { display: grid; grid-template-columns: 1fr 1fr; gap: 5px 12px; margin-top: 4px; }
  .test { font-size: 10px; letter-spacing: 0.08em; } .test.ok { color: #2fa971; } .test.bad { color: var(--scarlet); }
  .sbtns { display: flex; gap: 8px; margin-top: 4px; }
  .sbtn { flex: 1; background: none; border: 1px solid var(--ink-dim); color: var(--ink-dim); font: inherit; font-size: 11px; letter-spacing: 0.14em; padding: 8px; cursor: pointer; }
  .sbtn.go { border-color: var(--scarlet); color: var(--scarlet); } .sbtn:hover:not(:disabled) { color: var(--ink); border-color: var(--ink); } .sbtn.go:hover { background: rgba(225,6,0,0.12); }
  .sbtn:disabled { opacity: 0.4; cursor: default; }

  .ledger { flex: 1; overflow-y: auto; padding: 13px 14px; min-height: 90px; }
  .op.companion .ledger { min-height: 120px; }
  .hint { color: var(--ink-dim); font-size: 10px; letter-spacing: 0.16em; }
  .hint ul { margin: 11px 0 0; padding: 0; list-style: none; display: grid; gap: 6px; }
  .hint li { color: var(--ink-dim); opacity: 0.6; font-size: 12px; letter-spacing: 0.01em; text-transform: none; font-family: var(--font-mono); }
  .entry { display: flex; gap: 8px; padding: 4px 0; font-size: 13px; line-height: 1.45; }
  .op.companion .entry { font-size: 12px; }
  .entry .mk { flex: 0 0 auto; width: 11px; color: var(--ink-dim); }
  .entry.you { color: var(--ink); } .entry.you .mk { color: var(--ink-dim); }
  .entry.say { color: var(--ink); } .entry.say .mk { color: #2fa971; }
  .entry.step .tx { color: var(--ink-dim); }
  .entry.ask { color: #e0a02e; } .entry.error { color: var(--scarlet); }

  .prompt { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-top: 1px solid rgba(255,255,255,0.08); }
  .prompt.live { box-shadow: inset 0 2px 0 -1px var(--scarlet); }
  .mic { width: 36px; height: 36px; border-radius: 50%; border: 1px solid rgba(255,255,255,0.2); background: rgba(0,0,0,0.3); display: flex; align-items: center; justify-content: center; cursor: pointer; flex: 0 0 auto; }
  .mic .orb { width: 11px; height: 11px; border-radius: 50%; background: var(--ink-dim); transition: background 0.2s; }
  .mic.on { border-color: var(--scarlet); } .mic.on .orb { background: var(--scarlet); box-shadow: 0 0 10px var(--scarlet); animation: listen 1s ease-in-out infinite; }
  @keyframes listen { 0%,100% { transform: scale(0.8); opacity: 0.7; } 50% { transform: scale(1.25); opacity: 1; } }
  .prompt input { flex: 1; min-width: 0; background: rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.14); color: var(--ink); font: inherit; font-size: 13px; padding: 8px 11px; }
  .prompt input.interim { color: var(--ink-dim); font-style: italic; }
  .prompt input:focus { outline: none; border-color: rgba(225,6,0,0.5); }
  .go { background: none; border: 1px solid var(--scarlet); color: var(--scarlet); font: inherit; font-size: 11px; letter-spacing: 0.14em; padding: 7px 13px; cursor: pointer; flex: 0 0 auto; }
  .go:hover:not(:disabled) { background: rgba(225,6,0,0.12); }
  .go:disabled { opacity: 0.35; cursor: default; border-color: var(--ink-dim); color: var(--ink-dim); }
  @media (prefers-reduced-motion: reduce) { .op, .dot, .mic.on .orb { animation: none !important; } }
</style>
