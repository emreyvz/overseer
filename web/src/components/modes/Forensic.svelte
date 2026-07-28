<script lang="ts">
  // Attribute-based forensic search (item 11). Select params → cinematic scan across
  // all feeds → match found → that camera expands to POV with the target card.
  import { onDestroy, onMount } from 'svelte'
  import { get } from 'svelte/store'
  import { cameras, activeCam, mode, selectedDetection, triggerGlitch, forensicSeed, flashBanner, matchHighlight } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { sendCommand } from '../../lib/ws'
  import { SIM } from '../../lib/sim'
  import { api } from '../../lib/api'
  import { matchBanner } from '../../lib/match'
  import { matchMinScore } from '../../lib/feedback'
  import { watchlist } from '../../lib/watchlist'
  import type { Detection } from '../../lib/types'
  import LiveThumb from '../LiveThumb.svelte'

  // Simple, guided search: pick WHAT you're looking for, then just colour (+ height
  // for people). Icons carry the primary choice so it isn't a wall of chips.
  const KINDS = [
    { k: 'person', icon: '👤', label: 'PERSON' },
    { k: 'vehicle', icon: '🚗', label: 'VEHICLE' },
    { k: 'animal', icon: '🐾', label: 'ANIMAL' },
  ]
  const COLORS = ['BLACK', 'WHITE', 'GRAY', 'RED', 'BLUE', 'GREEN', 'YELLOW', 'BROWN']
  const HEIGHTS = ['SHORT', 'MEDIUM', 'TALL']

  let q = $state('')
  let kind = $state<'' | 'person' | 'vehicle' | 'animal'>('')
  let color = $state('')
  let height = $state('')
  let camFilter = $state('')
  let timeFilter = $state<'' | '1h' | '24h' | '7d'>('')
  let more = $state(false)   // camera/time filters tucked away by default
  let phase = $state<'idle' | 'scan' | 'match'>('idle')
  let matchId = $state<string | null>(null)
  let plateQ = $state('')
  const timers: ReturnType<typeof setTimeout>[] = []

  // Search live cameras for a vehicle by licence plate (ANPR). On a hit, jump to that
  // camera and drop the green located-match brackets so the operator sees it at once.
  async function searchByPlate() {
    const p = plateQ.trim()
    if (!p) { sfx('error'); flashBanner('ENTER A PLATE', true, 1400); return }
    if (SIM) { flashBanner('PLATE SEARCH NEEDS LIVE CAMERAS', true, 1600); return }
    sfx('sonar'); phase = 'scan'; matchId = null
    try {
      const res = await api.plateMatch(p)
      const best = res.matches?.[0]
      if (!best) { phase = 'idle'; sfx('error'); flashBanner('PLATE NOT FOUND IN ANY LIVE FEED', true, 1800); return }
      matchId = best.camId; phase = 'match'; sfx('ping', { critical: true, volume: 0.5 })
      activeCam.set(best.camId)
      const cam = $cameras.find((c) => c.id === best.camId)
      if (!SIM && cam) sendCommand(`connect:${cam.name}`)
      timers.push(setTimeout(() => {
        triggerGlitch(200)
        selectedDetection.set({ id: 'PLATE.MATCH', cls: 'vehicle', bbox: best.bbox, conf: best.score,
          severity: 'info', klass: 'TARGET', caseAlias: `PLATE ${best.plate}`, plate: best.plate })
        matchHighlight.set({ camId: best.camId, bbox: best.bbox, ts: Date.now() })
        phase = 'idle'; mode.set('pov')
        flashBanner(`PLATE ${best.plate} · ${best.cam}`, false, 1700)
      }, 900))
    } catch { phase = 'idle'; flashBanner('PLATE SEARCH FAILED', true, 1400) }
  }

  const sel = (cur: string, v: string) => { sfx('click', { volume: 0.25 }); return cur === v ? '' : v }
  const query = () => [q, kind, color, height].filter(Boolean).join(' ').trim()

  async function search() {
    const q0 = query()
    if (!q0) { sfx('error'); flashBanner('SELECT WHAT TO SEARCH FOR', true, 1400); return }
    sfx('sonar'); phase = 'scan'; matchId = null
    const now = Date.now() / 1000
    const spans: Record<string, number> = { '1h': 3600, '24h': 86400, '7d': 604800 }
    const minWait = new Promise((r) => setTimeout(r, 1800)) // hold the scan animation
    let hits: { source?: string }[] = []
    try {
      const [res] = await Promise.all([api.search(q0, {
        source: camFilter || undefined,
        start: timeFilter ? now - spans[timeFilter] : undefined,
        end: timeFilter ? now : undefined,
      }), minWait])
      hits = (res?.hits as any) ?? []
    } catch { await minWait }
    // Honest result: only reveal a match when the query actually returned one.
    if (hits.length === 0) { phase = 'idle'; sfx('error'); flashBanner('NO MATCH FOUND', true, 1700); return }
    const src = (hits[0] as any).source || camFilter
    matchId = ($cameras.find((c) => c.id === src)?.id) ?? (camFilter || ($cameras.find((c) => c.health === 'online')?.id ?? $cameras[0]?.id ?? null))
    phase = 'match'; sfx('ping', { critical: true, volume: 0.5 })   // found cue (bypass the 2-sound cap)
    timers.push(setTimeout(reveal, 800))
  }

  function reveal() {
    triggerGlitch(220); sfx('glitch')
    if (matchId) {
      activeCam.set(matchId)
      const cam = $cameras.find((c) => c.id === matchId)
      if (!SIM && cam?.health === 'online') sendCommand(`connect:${cam.name}`)
    }
    const det: Detection = {
      id: `TK_${Math.floor(Math.random() * 900 + 100)}.MATCH`, cls: (kind || 'person') as any, bbox: [0.44, 0.3, 0.12, 0.34],
      conf: 0.91, severity: 'info', klass: 'TARGET', caseAlias: 'MATCH',
      attrs: { upper_color: color.toLowerCase() || 'navy', height: (height.toLowerCase() as any) || 'medium' },
    }
    selectedDetection.set(det)
    if (matchId) matchHighlight.set({ camId: matchId, bbox: det.bbox, ts: Date.now() })
    phase = 'idle'; mode.set('pov')
  }
  // Search-by-image: real visual matching. The uploaded photo is matched against
  // live camera frames by image processing (backend /api/visualmatch), not a colour.
  async function searchByImage(e: Event) {
    const input = e.target as HTMLInputElement
    const file = input.files?.[0]
    if (!file) return
    const dataUrl: string = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result as string); r.readAsDataURL(file) })
    input.value = '' // allow re-picking the same file
    sfx('sonar'); phase = 'scan'; matchId = null
    const minWait = new Promise((r) => setTimeout(r, 1800))
    try {
      const [res] = await Promise.all([api.visualMatch(dataUrl, kind || undefined, matchMinScore(kind)), minWait])
      const best = res.matches?.[0]
      if (best) {
        matchId = best.camId; phase = 'match'; sfx('ping', { critical: true, volume: 0.5 })
        activeCam.set(best.camId)
        const cam = $cameras.find((c) => c.id === best.camId)
        if (!SIM && cam) sendCommand(`connect:${cam.name}`)
        timers.push(setTimeout(() => {
          triggerGlitch(200)
          selectedDetection.set({ id: 'IMG.MATCH', cls: (kind || 'person') as any, bbox: best.bbox, conf: best.score, severity: 'info', klass: 'TARGET', caseAlias: 'IMAGE MATCH' })
          matchHighlight.set({ camId: best.camId, bbox: best.bbox, ts: Date.now() })
          phase = 'idle'; mode.set('pov')
          flashBanner(matchBanner(best.cam, best.score, best.ambiguous), false, 1700)
        }, 900))
      } else { phase = 'idle'; flashBanner('IMAGE NOT FOUND IN ANY LIVE FEED', true, 1800) }
    } catch { phase = 'idle'; flashBanner('IMAGE SEARCH FAILED', true, 1400) }
  }

  // Search for an existing enrolled target across all cameras by its image.
  let scanning = $state('')
  async function locateEntity(e: (typeof $watchlist)[number]) {
    if (!e.image || SIM) { sfx('error'); flashBanner('NO IMAGE FOR THIS TARGET', true, 1400); return }
    scanning = e.id; sfx('sonar'); phase = 'scan'; matchId = null
    try {
      const res = await api.visualMatch(e.image, e.kind, matchMinScore(e.kind))
      const best = res.matches?.[0]
      if (best) {
        matchId = best.camId; phase = 'match'; sfx('ping', { critical: true, volume: 0.5 })
        activeCam.set(best.camId)
        const cam = $cameras.find((c) => c.id === best.camId)
        if (!SIM && cam) sendCommand(`connect:${cam.name}`)
        setTimeout(() => {
          triggerGlitch(200)
          selectedDetection.set({ id: `WL.${e.id}`, cls: (e.kind === 'pet' ? 'animal' : e.kind) as any, bbox: best.bbox,
            conf: best.score, severity: e.threat === 'threat' ? 'critical' : 'info', klass: 'TARGET', caseAlias: e.name })
          matchHighlight.set({ camId: best.camId, bbox: best.bbox, ts: Date.now() })
          phase = 'idle'; mode.set('pov')
          flashBanner(matchBanner(best.cam, best.score, best.ambiguous), false, 1600)
        }, 900)
      } else { phase = 'idle'; flashBanner('NOT FOUND IN ANY LIVE FEED', true, 1600) }
    } catch { phase = 'idle'; flashBanner('MATCH FAILED', true, 1400) }
    scanning = ''
  }

  onMount(() => {
    const seed = get(forensicSeed)
    if (seed) { q = seed; forensicSeed.set(''); setTimeout(search, 300) }
  })
  onDestroy(() => timers.forEach(clearTimeout))
</script>

<section class="forensic">
  <div class="hdr caps"><span class="hot">///</span> FORENSIC SEARCH <span class="hint">ESC · POV</span></div>

  <!-- 1 · what are you looking for -->
  <div class="ask caps">WHAT ARE YOU LOOKING FOR?</div>
  <div class="kinds">
    {#each KINDS as o}
      <button class="kind" class:on={kind === o.k} onclick={() => (kind = sel(kind, o.k) as any)}>
        <span class="ki">{o.icon}</span><span class="kl caps">{o.label}</span>
      </button>
    {/each}
  </div>

  <!-- 2 · colour (always) + height (people) -->
  <div class="grp">
    <div class="gl caps">COLOUR</div>
    <div class="chips">
      {#each COLORS as c}<button class="chip caps sw-{c.toLowerCase()}" class:on={color === c} onclick={() => (color = sel(color, c))}><span class="dotc"></span>{c}</button>{/each}
    </div>
  </div>
  {#if kind === 'person' || kind === ''}
    <div class="grp">
      <div class="gl caps">HEIGHT</div>
      <div class="chips">{#each HEIGHTS as hh}<button class="chip caps" class:on={height === hh} onclick={() => (height = sel(height, hh))}>{hh}</button>{/each}</div>
    </div>
  {/if}

  {#if $watchlist.length}
    <div class="grp">
      <div class="gl caps">◈ OR FIND A SAVED TARGET · VISUAL MATCH</div>
      <div class="wlrow">
        {#each $watchlist.slice(0, 14) as e (e.id)}
          <button class="wlcard" class:on={scanning === e.id} onclick={() => locateEntity(e)} title={e.name}>
            {#if e.image}<img src={e.image} alt="" />{:else}<div class="wlni caps">?</div>{/if}
            <span class="wln caps">{e.name}</span>
          </button>
        {/each}
      </div>
    </div>
  {/if}

  <!-- 3 · free text + optional camera/time -->
  <div class="bar">
    <span class="lead caps hot">OR TYPE_</span>
    <input bind:value={q} class="type" placeholder="E.G. RED JACKET"
      onkeydown={(e) => e.key === 'Enter' && search()} spellcheck="false" />
    <label class="go caps img">⬆ IMAGE<input type="file" accept="image/*" onchange={searchByImage} hidden /></label>
    <button class="go caps" onclick={search}>◎ SEARCH</button>
    <input bind:value={plateQ} class="type plateq" placeholder="PLATE #"
      onkeydown={(e) => e.key === 'Enter' && searchByPlate()} spellcheck="false" />
    <button class="go caps" onclick={searchByPlate}>▤ PLATE</button>
  </div>

  <button class="morebtn caps" onclick={() => (more = !more)}>{more ? '▾' : '▸'} CAMERA / TIME FILTERS</button>
  {#if more}
    <div class="filters">
      <div class="grp">
        <div class="gl caps">CAMERA</div>
        <div class="chips">
          <button class="chip caps" class:on={camFilter === ''} onclick={() => (camFilter = '')}>ALL</button>
          {#each $cameras as c}<button class="chip caps" class:on={camFilter === c.id} onclick={() => (camFilter = c.id)}>{c.name}</button>{/each}
        </div>
      </div>
      <div class="grp">
        <div class="gl caps">TIME</div>
        <div class="chips">
          {#each [['', 'ANY'], ['1h', 'LAST HOUR'], ['24h', 'LAST DAY'], ['7d', 'LAST WEEK']] as [v, l]}
            <button class="chip caps" class:on={timeFilter === v} onclick={() => (timeFilter = v as any)}>{l}</button>
          {/each}
        </div>
      </div>
    </div>
  {/if}
</section>

{#if phase !== 'idle'}
  <div class="scan">
    <div class="stitle caps">{phase === 'match' ? 'MATCH LOCATED_' : `SCANNING ${$cameras.length} FEEDS_`}</div>
    <div class="feeds">
      {#each $cameras as c (c.id)}
        <div class="fcell" class:match={phase === 'match' && matchId === c.id} class:dim={phase === 'match' && matchId !== c.id}>
          <div class="fthumb">{#if c.health === 'online'}<LiveThumb id={c.id} fps={3} />{:else}<div class="fno caps">NO SIGNAL</div>{/if}</div>
          <div class="fname caps">{c.name}</div>
          {#if phase === 'match' && matchId === c.id}<div class="fbadge caps">▼ TARGET</div>{/if}
        </div>
      {/each}
    </div>
    {#if phase === 'scan'}<div class="sweep"></div>{/if}
  </div>
{/if}

<style>
  .forensic { position: absolute; inset: 0; z-index: var(--z-boot); background: #050607; color: var(--ink); overflow: auto; padding: 22px 30px; }
  .hdr { font-size: var(--fs-title); letter-spacing: var(--tracking); margin-bottom: 16px; }
  .hdr .hint { float: right; font-size: var(--fs-micro); color: var(--ink-ghost); }
  .bar { display: flex; align-items: center; gap: 12px; background: #000; border: 1px solid var(--ink); padding: 10px 14px; }
  .lead { font-size: var(--fs-label); }
  input { flex: 1; background: none; border: none; outline: none; color: var(--ink); font-family: var(--font-type);
    font-size: var(--fs-banner); letter-spacing: var(--tracking); text-transform: uppercase; caret-color: var(--scarlet); }
  input::placeholder { color: var(--ink-ghost); }
  .plateq { max-width: 150px; letter-spacing: 0.14em; }
  .go { padding: 4px 14px; border: 1px solid var(--ink); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .go:hover { background: var(--scarlet); border-color: var(--scarlet); color: #fff; }
  .go.img { cursor: crosshair; }

  .ask { font-size: var(--fs-label); color: var(--ink-dim); letter-spacing: var(--tracking); margin: 4px 0 10px; }
  .kinds { display: flex; gap: 12px; margin-bottom: 18px; }
  .kind { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 14px 26px; border: 1px solid var(--hairline); background: #07090b; cursor: crosshair; }
  .kind .ki { font-size: 30px; filter: grayscale(0.15); }
  .kind .kl { font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink-dim); }
  .kind:hover { border-color: var(--ink-dim); }
  .kind:hover .kl { color: var(--ink); }
  .kind.on { border-color: var(--scarlet); background: rgba(225,6,0,0.08); box-shadow: 0 0 18px rgba(225,6,0,0.2); }
  .kind.on .kl { color: var(--scarlet); }

  .dotc { display: inline-block; width: 9px; height: 9px; margin-right: 6px; border: 1px solid var(--hairline); vertical-align: middle; }
  .sw-black .dotc { background: #111; } .sw-white .dotc { background: #eee; } .sw-gray .dotc { background: #888; }
  .sw-red .dotc { background: #d21; } .sw-blue .dotc { background: #2a6cf0; } .sw-green .dotc { background: #2c9; }
  .sw-yellow .dotc { background: #ec4; } .sw-brown .dotc { background: #85502a; }

  .wlrow { display: flex; flex-wrap: wrap; gap: 8px; }
  .wlcard { width: 72px; border: 1px solid var(--hairline); background: #07090b; padding: 0; cursor: crosshair; overflow: hidden; }
  .wlcard:hover { border-color: var(--cyan); }
  .wlcard.on { border-color: var(--scarlet); box-shadow: 0 0 12px var(--scarlet-glow); }
  .wlcard img { display: block; width: 100%; aspect-ratio: 3/4; object-fit: cover; }
  .wlni { aspect-ratio: 3/4; display: grid; place-content: center; color: var(--ink-ghost); font-size: 18px; }
  .wln { display: block; padding: 3px 4px; font-size: 7px; color: var(--ink-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .morebtn { margin: 12px 0 4px; background: none; border: none; color: var(--ink-dim); font-size: var(--fs-micro); letter-spacing: var(--tracking); cursor: pointer; }
  .morebtn:hover { color: var(--ink); }
  .filters { display: flex; flex-wrap: wrap; gap: 20px; margin: 8px 0; }
  .gl { font-size: var(--fs-micro); color: var(--ink-ghost); margin-bottom: 5px; }
  .chips { display: flex; flex-wrap: wrap; gap: 5px; }
  .chip { padding: 3px 9px; border: 1px solid var(--hairline); font-size: var(--fs-micro); letter-spacing: 0.1em; color: var(--ink-dim); cursor: crosshair; }
  .chip.on { background: var(--ink); color: var(--scarlet-ink); border-color: var(--ink); }

  /* scan overlay */
  .scan { position: fixed; inset: 0; z-index: var(--z-cmd); background: rgba(2,3,4,0.92); overflow: hidden;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 20px; animation: fin 200ms; }
  @keyframes fin { from { opacity: 0; } }
  .stitle { font-family: var(--font-display); font-weight: 700; font-size: 22px; letter-spacing: var(--tracking-wide); color: var(--scarlet); text-shadow: 0 0 16px var(--scarlet-glow); }
  .feeds { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 180px)); gap: 12px; max-width: 900px; justify-content: center; }
  .fcell { border: 1px solid var(--hairline); transition: transform 300ms var(--ease), opacity 300ms; }
  .fcell.match { transform: scale(1.5); border-color: var(--scarlet); box-shadow: 0 0 30px var(--scarlet-glow); z-index: 2; }
  .fcell.dim { opacity: 0.18; }
  .fthumb { position: relative; aspect-ratio: 16/9; overflow: hidden; background: #05070a; }
  .fno { position: absolute; inset: 0; display: grid; place-content: center; color: var(--ink-ghost); font-size: 9px; }
  .fname { padding: 3px 6px; font-size: 8px; color: var(--ink-dim); }
  .fbadge { position: absolute; top: 4px; left: 4px; background: var(--scarlet); color: #fff; font-size: 8px; padding: 1px 5px; letter-spacing: 0.1em; }
  .sweep { position: absolute; left: 0; right: 0; height: 3px; background: linear-gradient(90deg, transparent, var(--scarlet), transparent);
    box-shadow: 0 0 16px var(--scarlet); animation: sweep 1.7s linear; }
  @keyframes sweep { from { top: 0; } to { top: 100%; } }
</style>
