<script lang="ts">
  // AI assistant console (GLM / OpenAI-compatible). Natural-language search routes
  // to the forensic scan; anything else is a chat. Overseer-styled, terse.
  import { aiOpen, forensicSeed, mode, stage, timeline, alerts, activeCam, flashBanner } from '../lib/stores'
  import { api, type AiRule } from '../lib/api'
  import { aiStatus, refreshAiStatus, aiOn, AI_FEATURES } from '../lib/ai'
  import { routeCommand, runPlan, operatorLog } from '../lib/operator'
  import { sfx } from '../lib/audio'
  import { get } from 'svelte/store'
  import { onMount, tick } from 'svelte'

  type Msg = { who: 'you' | 'ai' | 'sys'; text: string; filter?: any; rule?: AiRule; ruleText?: string }
  let msgs = $state<Msg[]>([])
  let input = $state('')
  let busy = $state(false)
  let box = $state<HTMLDivElement>()
  const anyFeature = $derived(!!$aiStatus.enabled)  // provider usable at all

  // ---- provider settings (catalog 226) + per-feature toggles (graceful degradation)
  let settings = $state(false)
  let saving = $state(false)
  let testMsg = $state<{ ok: boolean; detail: string } | null>(null)
  const PRESETS: Record<string, { base: string; model: string; vision: string }> = {
    GLM: { base: 'https://api.z.ai/api/coding/paas/v4', model: 'glm-4.6', vision: 'glm-4.6v' },
    OPENAI: { base: 'https://api.openai.com/v1', model: 'gpt-4o-mini', vision: 'gpt-4o-mini' },
    CUSTOM: { base: '', model: '', vision: '' },
  }
  let cfg = $state({ provider: 'GLM', base_url: '', api_key: '', model: '', vision_model: '' })
  let featForm = $state<Record<string, boolean>>({})

  function loadCfgFromStatus() {
    const s = $aiStatus
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
  function openSettings() { loadCfgFromStatus(); testMsg = null; settings = true; sfx('click', { volume: 0.3 }) }

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
      aiStatus.set(s); settings = false; sfx('ping')
      flashBanner(s.enabled ? `ASSISTANT SAVED · ${s.model}` : 'SAVED · KEY REQUIRED', !s.enabled, 1600)
      msgs = [...msgs, { who: 'sys', text: s.enabled ? `ASSISTANT ONLINE · ${s.model}` : 'ASSISTANT OFFLINE · KEY REQUIRED' }]
    } catch { flashBanner('SAVE FAILED', true, 1400); sfx('error') }
    saving = false
  }

  onMount(async () => {
    const s = await refreshAiStatus()
    msgs = [{ who: 'sys', text: s.enabled
      ? `ASSISTANT ONLINE · ${s.model ?? ''} · ASK, SEARCH OR SET A RULE IN PLAIN LANGUAGE`
      : 'ASSISTANT OFFLINE · ⚙ CONFIGURE A PROVIDER' }]
  })

  // Intent routing. Rule-setting beats search beats event-question beats chat; each hop
  // is skipped if its feature is off, so the request falls through to whatever IS on.
  const SEARCHY = /\b(find|search|locate|show|bul|ara|göster|giyen|wearing|jacket|ceket|car|vehicle|araba|person|person|insan|dog|köpek|animal|hayvan)\b/i
  const RULEY = /(alarm ver|alarm oluştur|kural (ekle|oluştur|yap|tanımla|kur)|alert me|notify me|bana (haber ver|bildir|uyar)|girerse|girince|olursa|geçerse|aşınca|aşarsa|tespit edil)/i
  const ASKY = /(ne zaman|kim |kaç (kez|kere|defa)|geçen|dün|önce|when did|who was|how many|last time|hangi kamera|oldu mu|gördü)/i

  async function send() {
    const q = input.trim()
    if (!q || busy) return
    input = ''; msgs = [...msgs, { who: 'you', text: q }]; busy = true; sfx('click', { volume: 0.3 })
    await scroll()
    const s = $aiStatus
    try {
      // AI Operator: instant deterministic action / navigation commands (no LLM round-trip).
      const plan = routeCommand(q)
      if (plan) {
        await runPlan(plan)
        const tail = get(operatorLog).slice(-6).filter((e) => e.kind !== 'error')
        msgs = [...msgs, { who: 'sys', text: (tail.map((e) => e.text).join(' · ') || 'DONE').toUpperCase() }]
        // step out of the way for navigation actions so the operator sees the screen it opened
        const informational = (plan.steps ?? []).every((st) => st.action === 'describe_scene' || st.action === 'say')
        if (!informational) aiOpen.set(false)
        busy = false; await scroll(); return
      }
      if (aiOn(s, 'rules') && RULEY.test(q)) {
        await proposeRule(q)
      } else if (aiOn(s, 'search') && SEARCHY.test(q)) {
        const { filter, disabled } = await api.aiQuery(q)
        if (disabled) await chat(q)
        else if (filter && (filter.kind || filter.color || filter.height)) {
          msgs = [...msgs, { who: 'ai', text: `PARSED → ${[filter.kind, filter.color, filter.height, filter.time].filter(Boolean).join(' · ').toUpperCase() || '—'}`, filter }]
        } else await chat(q)
      } else if (aiOn(s, 'semantic') && ASKY.test(q) && $timeline.length) {
        await semanticSearch(q)
      } else if (aiOn(s, 'chat')) {
        await chat(q)
      } else {
        msgs = [...msgs, { who: 'sys', text: 'NO MATCHING ASSISTANT FEATURE IS ENABLED · ⚙ SETTINGS' }]
      }
    } catch { msgs = [...msgs, { who: 'sys', text: 'REQUEST FAILED' }] }
    busy = false; await scroll()
  }

  async function chat(q: string) {
    const { reply, disabled } = await api.aiChat(q, 'You are a concise surveillance operations assistant. Answer in 1-2 sentences.')
    msgs = [...msgs, disabled ? { who: 'sys', text: 'CHAT IS DISABLED · ⚙ SETTINGS' } : { who: 'ai', text: reply || 'NO RESPONSE' }]
  }

  function runSearch(f: any) {
    sfx('sonar')
    forensicSeed.set([f.kind, f.color, f.height].filter(Boolean).join(' '))
    aiOpen.set(false); stage.set('live'); mode.set('forensic')
  }

  // --- idea 1: natural-language → alert rule (preview, then create) -----------------
  const SEV_MARK: Record<string, string> = { info: '·', warning: '▲', critical: '◆' }
  function ruleLine(r: AiRule) {
    const bits = [r.event_type]
    if (r.source_id != null) bits.push(`CAM ${r.source_id}`)
    if (r.zone_id != null) bits.push(`ZONE ${r.zone_id}`)
    if (r.min_count != null) bits.push(`≥${r.min_count}`)
    return `${SEV_MARK[r.severity ?? 'warning'] ?? '▲'} ${(r.severity ?? 'warning').toUpperCase()} · ${bits.join(' · ')}`
  }
  async function proposeRule(q: string) {
    const { rule, disabled } = await api.aiRule(q, false)
    if (disabled) { await chat(q); return }
    if (!rule) { msgs = [...msgs, { who: 'ai', text: "COULDN'T FORM A RULE FROM THAT — TRY E.G. 'ALERT IF A VEHICLE ENTERS AT NIGHT'" }]; return }
    msgs = [...msgs, { who: 'ai', text: `RULE → ${ruleLine(rule)}`, rule, ruleText: q }]
  }
  async function applyRule(m: Msg) {
    if (!m.rule) return
    sfx('click'); busy = true
    try {
      const { created } = await api.aiRule(m.ruleText ?? '', true, m.rule)
      msgs = [...msgs, created ? { who: 'sys', text: `RULE CREATED · #${created} · NOW LIVE` } : { who: 'sys', text: 'RULE NOT CREATED' }]
      if (created) { flashBanner('ALERT RULE CREATED', false, 1500); sfx('ping') }
    } catch { msgs = [...msgs, { who: 'sys', text: 'RULE CREATE FAILED' }] }
    busy = false; await scroll()
  }

  // --- idea 10: semantic event search ----------------------------------------------
  async function semanticSearch(q: string) {
    const ev = $timeline.slice(0, 60).map((e) => ({ ts: new Date(e.ts).toLocaleTimeString(), type: e.type, cam: e.cam, label: e.label }))
    const { result, disabled } = await api.aiSearchEvents(q, ev)
    if (disabled) { await chat(q); return }
    msgs = [...msgs, { who: 'ai', text: result?.answer || 'NO MATCHING EVENTS' }]
  }

  // --- idea 5: multi-alert correlation ---------------------------------------------
  async function correlate() {
    if (busy) return
    busy = true; msgs = [...msgs, { who: 'you', text: 'CORRELATE RECENT ALERTS' }]; await scroll()
    const al = $alerts.slice(0, 20).map((a) => ({ ts: new Date(a.ts).toLocaleTimeString(), severity: a.severity, type: a.type, cam: a.cam, summary: a.summary }))
    if (!al.length) { msgs = [...msgs, { who: 'sys', text: 'NO ALERTS TO CORRELATE' }]; busy = false; await scroll(); return }
    try {
      const { result, disabled } = await api.aiCorrelate(al)
      if (disabled) msgs = [...msgs, { who: 'sys', text: 'CORRELATION IS DISABLED · ⚙ SETTINGS' }]
      else if (result?.incident) msgs = [...msgs, { who: 'ai', text: `◆ ${result.title || 'INCIDENT'} — ${result.assessment || ''}${result.action ? `\n▸ ${result.action}` : ''}${result.cams?.length ? `\nCAMS: ${result.cams.join(', ')}` : ''}` }]
      else msgs = [...msgs, { who: 'ai', text: result?.assessment || 'NO LINKED INCIDENT — ALERTS APPEAR INDEPENDENT' }]
    } catch { msgs = [...msgs, { who: 'sys', text: 'CORRELATION FAILED' }] }
    busy = false; await scroll()
  }

  async function summarize() {
    if (busy) return
    busy = true; msgs = [...msgs, { who: 'you', text: 'SUMMARIZE RECENT ACTIVITY' }]; await scroll()
    const ev = [
      ...$alerts.slice(0, 20).map((a) => ({ type: a.type, cam: a.cam, label: a.summary })),
      ...$timeline.slice(0, 20).map((e) => ({ type: e.type, cam: e.cam, label: e.label })),
    ]
    try { const { summary, disabled } = await api.aiSummarize(ev); msgs = [...msgs, disabled ? { who: 'sys', text: 'SUMMARY IS DISABLED · ⚙ SETTINGS' } : { who: 'ai', text: summary || 'NOTHING TO SUMMARIZE' }] }
    catch { msgs = [...msgs, { who: 'sys', text: 'SUMMARY FAILED' }] }
    busy = false; await scroll()
  }

  async function describe() {
    if (busy || !$activeCam) { flashBanner('NO ACTIVE CAMERA', true, 1200); return }
    busy = true; msgs = [...msgs, { who: 'you', text: 'DESCRIBE THE ACTIVE FEED' }]; await scroll()
    try { const { description, disabled } = await api.aiDescribe($activeCam); msgs = [...msgs, disabled ? { who: 'sys', text: 'VISION IS DISABLED · ⚙ SETTINGS' } : { who: 'ai', text: description || 'NO SCENE DESCRIPTION' }] }
    catch { msgs = [...msgs, { who: 'sys', text: 'DESCRIBE FAILED' }] }
    busy = false; await scroll()
  }

  async function scroll() { await tick(); if (box) box.scrollTop = box.scrollHeight }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="scrim" role="presentation" onclick={() => aiOpen.set(false)}>
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="ai" role="dialog" aria-modal="true" tabindex="-1" onclick={(e) => e.stopPropagation()}>
    <div class="hdr caps"><span class="hot">///</span> ASSISTANT
      <span class="st" class:on={$aiStatus.enabled}>{$aiStatus.enabled ? `● ${$aiStatus.model}` : '○ OFFLINE'}</span>
      <button class="gear" class:on={settings} onclick={() => (settings ? (settings = false) : openSettings())} aria-label="settings" title="PROVIDER & FEATURE SETTINGS">⚙</button>
      <button class="x" onclick={() => aiOpen.set(false)} aria-label="close">×</button>
    </div>

    {#if settings}
    <div class="cfg">
      <div class="crow"><span class="cl caps">PROVIDER</span>
        <div class="segs">{#each Object.keys(PRESETS) as p}<button class="seg caps" class:on={cfg.provider === p} onclick={() => applyPreset(p)}>{p}</button>{/each}</div>
      </div>
      <label class="crow"><span class="cl caps">BASE URL</span>
        <input class="ci" bind:value={cfg.base_url} placeholder="https://api.z.ai/api/coding/paas/v4" spellcheck="false" autocomplete="off" /></label>
      <label class="crow"><span class="cl caps">API KEY</span>
        <input class="ci" type="password" bind:value={cfg.api_key} placeholder={$aiStatus.keyHint ? `STORED ${$aiStatus.keyHint} · LEAVE BLANK TO KEEP` : 'PASTE KEY'} spellcheck="false" autocomplete="off" /></label>
      <label class="crow"><span class="cl caps">MODEL</span>
        <input class="ci" bind:value={cfg.model} placeholder="glm-4.6" spellcheck="false" autocomplete="off" /></label>
      <label class="crow"><span class="cl caps">VISION</span>
        <input class="ci" bind:value={cfg.vision_model} placeholder="glm-4.6v (optional)" spellcheck="false" autocomplete="off" /></label>

      <div class="fsec caps">◆ FEATURES — TOGGLE INDIVIDUALLY</div>
      <div class="feats">
        {#each AI_FEATURES as f}
          <button class="feat" class:on={featForm[f.key]} onclick={() => (featForm[f.key] = !featForm[f.key])} type="button">
            <span class="fsw" aria-hidden="true">{featForm[f.key] ? '◉' : '○'}</span>
            <span class="fbody"><span class="fl caps">{f.label}</span><span class="fd">{f.desc}</span></span>
          </button>
        {/each}
      </div>
      {#if testMsg}<div class="tmsg caps" class:ok={testMsg.ok}>{testMsg.ok ? '✓ ' : '✕ '}{testMsg.detail}</div>{/if}
      <div class="cbtns">
        <button class="cb caps" onclick={testCfg} disabled={saving}>◇ TEST</button>
        <button class="cb save caps" onclick={saveCfg} disabled={saving}>{saving ? 'SAVING…' : '● SAVE'}</button>
        <button class="cb ghost caps" onclick={() => (settings = false)} disabled={saving}>CANCEL</button>
      </div>
      <div class="chint caps">OPENAI-STANDARD · WORKS WITH ANY COMPATIBLE ENDPOINT</div>
    </div>
    {:else}
    <div class="log" bind:this={box}>
      {#each msgs as m}
        <div class="m {m.who}">
          <span class="who caps">{m.who === 'you' ? 'YOU' : m.who === 'ai' ? 'AI' : 'SYS'}</span>
          <span class="tx">{m.text}
            {#if m.filter}<button class="run caps" onclick={() => runSearch(m.filter)}>◎ RUN SEARCH ▸</button>{/if}
            {#if m.rule}<button class="run caps" onclick={() => applyRule(m)} disabled={busy}>＋ CREATE RULE ▸</button>{/if}
          </span>
        </div>
      {/each}
      {#if busy}<div class="m ai"><span class="who caps">AI</span><span class="tx thinking">···</span></div>{/if}
    </div>
    {#if aiOn($aiStatus, 'summarize') || aiOn($aiStatus, 'vision') || aiOn($aiStatus, 'correlate')}
    <div class="quick">
      {#if aiOn($aiStatus, 'summarize')}<button class="q caps" onclick={summarize} disabled={busy}>⟐ SUMMARIZE</button>{/if}
      {#if aiOn($aiStatus, 'vision')}<button class="q caps" onclick={describe} disabled={busy}>◇ DESCRIBE FEED</button>{/if}
      {#if aiOn($aiStatus, 'correlate')}<button class="q caps" onclick={correlate} disabled={busy}>⧉ CORRELATE ALERTS</button>{/if}
    </div>
    {/if}
    <div class="bar">
      <span class="lead caps hot">ASK_</span>
      <input bind:value={input} class="type" disabled={!anyFeature}
        placeholder={anyFeature ? 'ASK · SEARCH · SET A RULE — E.G. ALERT IF A VEHICLE ENTERS AT NIGHT' : 'PROVIDER OFFLINE · ⚙ CONFIGURE'}
        onkeydown={(e) => e.key === 'Enter' && send()} spellcheck="false" autocomplete="off" />
      <button class="go caps" onclick={send} disabled={busy || !anyFeature}>SEND</button>
    </div>
    {/if}
  </div>
</div>

<style>
  .scrim { position: absolute; inset: 0; z-index: var(--z-cmd); background: rgba(2,3,4,0.72); display: grid; place-items: center; animation: fin 180ms; }
  @keyframes fin { from { opacity: 0; } }
  .ai { width: min(680px, 94vw); height: min(70vh, 620px); background: #070809; border: 1px solid var(--ink);
    box-shadow: 0 0 44px rgba(0,0,0,0.7); display: flex; flex-direction: column; animation: up 200ms var(--ease); }
  @keyframes up { from { transform: translateY(12px); opacity: 0; } }
  .hdr { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-title); letter-spacing: var(--tracking-wide); color: var(--ink); }
  .hdr .hot { color: var(--scarlet); }
  .hdr .st { font-size: var(--fs-micro); color: var(--ink-ghost); letter-spacing: var(--tracking); }
  .hdr .st.on { color: var(--cyan); }
  .hdr .gear { margin-left: auto; font-size: 13px; color: var(--ink-dim); cursor: pointer; background: none; border: none; padding: 0 2px; }
  .hdr .gear:hover, .hdr .gear.on { color: var(--cyan); }
  .hdr .x { font-size: 17px; color: var(--ink-dim); cursor: pointer; } .hdr .x:hover { color: var(--scarlet); }

  /* provider settings (catalog 226) */
  .cfg { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
  .crow { display: grid; grid-template-columns: 74px 1fr; align-items: center; gap: 10px; }
  .cl { font-size: var(--fs-micro); color: var(--ink-ghost); letter-spacing: var(--tracking); }
  .ci { background: #000; border: 1px solid var(--hairline); outline: none; color: var(--ink); font-family: var(--font-mono);
    font-size: var(--fs-label); letter-spacing: 0.06em; padding: 6px 8px; }
  .ci:focus { border-color: var(--cyan); }
  .ci::placeholder { color: var(--ink-ghost); text-transform: none; letter-spacing: 0; }
  .segs { display: flex; gap: 5px; }
  .seg { padding: 5px 12px; border: 1px solid var(--hairline); color: var(--ink-dim); background: none; font-size: var(--fs-micro); letter-spacing: var(--tracking); cursor: pointer; }
  .seg:hover { border-color: var(--ink-dim); color: var(--ink); }
  .seg.on { border-color: var(--cyan); color: var(--cyan); background: rgba(0,180,210,0.08); }
  .tmsg { font-size: var(--fs-micro); letter-spacing: var(--tracking); color: var(--scarlet); padding: 2px 0; }
  .tmsg.ok { color: var(--cyan); }
  .cbtns { display: flex; gap: 8px; margin-top: 2px; }
  .cb { padding: 6px 14px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; font-size: var(--fs-micro); letter-spacing: var(--tracking); cursor: pointer; }
  .cb:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
  .cb.save { border-color: var(--cyan); color: var(--cyan); } .cb.save:hover:not(:disabled) { background: var(--cyan); color: #04070a; }
  .cb.ghost { margin-left: auto; } .cb:disabled { opacity: 0.4; }
  .chint { font-size: var(--fs-micro); color: var(--ink-ghost); letter-spacing: var(--tracking); margin-top: auto; }

  .fsec { font-size: var(--fs-micro); color: var(--cyan); letter-spacing: var(--tracking); margin-top: 4px; }
  .feats { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; }
  @media (max-width: 560px) { .feats { grid-template-columns: 1fr; } }
  .feat { display: flex; align-items: flex-start; gap: 8px; text-align: left; padding: 6px 8px; background: none;
    border: 1px solid var(--hairline); cursor: pointer; }
  .feat:hover { border-color: var(--ink-dim); }
  .feat.on { border-color: var(--cyan); background: rgba(0,180,210,0.06); }
  .feat .fsw { color: var(--ink-ghost); font-size: 12px; line-height: 1.3; }
  .feat.on .fsw { color: var(--cyan); }
  .fbody { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
  .fl { font-size: var(--fs-micro); color: var(--ink-dim); letter-spacing: var(--tracking); }
  .feat.on .fl { color: var(--ink); }
  .fd { font-size: var(--fs-micro); color: var(--ink-ghost); text-transform: none; letter-spacing: 0; line-height: 1.3; }
  .log { flex: 1; overflow-y: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
  .m { display: grid; grid-template-columns: 34px 1fr; gap: 10px; align-items: start; }
  .m .who { font-family: var(--font-mono); font-size: 8px; letter-spacing: 0.1em; padding-top: 3px; }
  .m.you .who { color: var(--ink-dim); } .m.ai .who { color: var(--cyan); } .m.sys .who { color: var(--ink-ghost); }
  .m .tx { font-size: var(--fs-label); line-height: 1.5; color: var(--ink); }
  .m.you .tx { color: var(--ink-dim); }
  .m.sys .tx { color: var(--ink-ghost); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .thinking { color: var(--cyan); animation: blink 1s steps(3) infinite; }
  @keyframes blink { 50% { opacity: 0.3; } }
  .run { display: block; margin-top: 8px; padding: 4px 10px; border: 1px solid var(--cyan); color: var(--cyan); font-size: var(--fs-micro); letter-spacing: var(--tracking); cursor: crosshair; }
  .run:hover { background: var(--cyan); color: #04070a; }
  .quick { display: flex; gap: 8px; padding: 8px 14px; border-top: 1px solid var(--hairline); }
  .q { padding: 5px 10px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: var(--fs-micro); letter-spacing: var(--tracking); background: none; cursor: pointer; }
  .q:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); } .q:disabled { opacity: 0.4; }
  .bar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-top: 1px solid var(--hairline); background: #000; }
  .lead { font-size: var(--fs-label); color: var(--scarlet); }
  input { flex: 1; background: none; border: none; outline: none; color: var(--ink); font-family: var(--font-type);
    font-size: var(--fs-banner); letter-spacing: var(--tracking); text-transform: uppercase; caret-color: var(--scarlet); }
  input::placeholder { color: var(--ink-ghost); }
  .go { padding: 5px 14px; border: 1px solid var(--ink); color: var(--ink); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .go:hover:not(:disabled) { background: var(--scarlet); border-color: var(--scarlet); color: #fff; } .go:disabled { opacity: 0.4; }
</style>
