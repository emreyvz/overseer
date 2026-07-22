<script lang="ts">
  // Overseer source picker shown after boot (items 1,3,4,5,6,7,8).
  import { cameras, pickerView, optimisticAddCamera, optimisticRemoveCamera, flashBanner } from '../lib/stores'
  import { sfx } from '../lib/audio'
  import { api } from '../lib/api'
  import type { Camera } from '../lib/types'
  import LiveThumb from './LiveThumb.svelte'

  let { onpick }: { onpick: (cam: Camera | null) => void } = $props()
  let leaving = $state(false)
  let manage = $state(false)
  let online = $state<Record<string, boolean>>({})  // live-frame status per camera

  function choose(cam: Camera | null) {
    if (leaving || manage) return
    leaving = true; sfx('glitch')
    setTimeout(() => onpick(cam), 300)
  }
  function toMap() { sfx('whoosh'); pickerView.set('map') }

  // — source management (item 8) —
  let fName = $state(''), fUrl = $state(''), editId = $state<string | null>(null)
  function resetForm() { fName = ''; fUrl = ''; editId = null }
  let busy = $state(false)
  async function submit() {
    const name = fName.trim(), url = fUrl.trim()
    if (!name || !url || busy) return
    sfx('click'); busy = true
    const eid = editId
    try {
      if (eid) {
        cameras.update((l) => l.map((c) => (c.id === eid ? { ...c, name, url } : c))) // optimistic
        await api.updateSource(eid, name, url)
        flashBanner('SOURCE SAVED', false, 1400)
      } else {
        const { id } = await api.addSource(name, url)
        optimisticAddCamera({ id: String(id), name, url, health: 'offline', fps: 0 })
        flashBanner('SOURCE ADDED', false, 1400)
      }
      resetForm()
    } catch { flashBanner('SAVE FAILED — SERVER OFFLINE', true, 1600) }
    busy = false
  }
  function edit(c: Camera) { editId = c.id; fName = c.name; fUrl = c.url ?? '' }
  function remove(c: Camera) {
    sfx('click')
    optimisticRemoveCamera(c.id)          // instant UI removal; heartbeat won't re-add it
    api.deleteSource(c.id).catch(() => {}) // fire-and-forget; server reconciles
    if (editId === c.id) resetForm()
  }
</script>

<div class="select" class:leaving>
  <div class="corner tl"></div><div class="corner tr"></div>
  <div class="corner bl"></div><div class="corner br"></div>

  <header>
    <div>
      <div class="title caps">SELECT OBSERVATION SOURCE</div>
      <div class="sub caps">SCANNING SOURCES<span class="cur"></span> · {$cameras.length} FEEDS</div>
    </div>
    <button class="mng caps" class:on={manage} onclick={() => { manage = !manage; resetForm() }}>⚙ MANAGE SOURCES</button>
  </header>

  {#if manage}
    <div class="stage">
      <div class="panel mgr">
        <div class="ph caps">{editId ? 'EDIT SOURCE' : 'ADD SOURCE'}</div>
        <div class="form">
          <label class="caps">NAME<input bind:value={fName} placeholder="e.g. LOBBY" spellcheck="false" /></label>
          <label class="caps">URL<input bind:value={fUrl} placeholder="http://…/video.mjpg · rtsp://… · youtube.com/watch?v=…" spellcheck="false" /></label>
          <div class="uhint caps">MJPEG · RTSP/RTMP · YOUTUBE LIVE</div>
          <div class="fbtn">
            <button class="b" onclick={submit} disabled={busy}>{busy ? '…' : editId ? 'SAVE' : 'ADD'}</button>
            {#if editId}<button class="b" onclick={resetForm}>CANCEL</button>{/if}
          </div>
        </div>
        <div class="ph caps">SOURCES</div>
        <div class="rows caps">
          {#each $cameras as c}
            <div class="row">
              <span class="dot" class:live={c.health === 'online'}></span>
              <span class="nm">{c.name}</span>
              <button class="mini" onclick={() => edit(c)}>EDIT</button>
              <button class="mini del" onclick={() => remove(c)}>DEL</button>
            </div>
          {/each}
          {#if $cameras.length === 0}<div class="empty">NO SOURCES</div>{/if}
        </div>
      </div>
    </div>
  {:else}
    <div class="stage">
      {#if $cameras.length === 0}
        <div class="mtempty caps">NO SOURCES<br /><span>USE ⚙ MANAGE SOURCES TO ADD ONE</span></div>
      {:else}
        <div class="grid">
          {#each $cameras as cam, i (cam.id)}
            <button class="card panel" class:off={online[cam.id] === false}
              style={`animation-delay:${i * 60}ms`} onclick={() => choose(cam)}>
              <div class="thumb">
                <LiveThumb id={cam.id} fps={4} onstatus={(o) => (online[cam.id] = o)} />
                <span class="ret"></span>
                <span class="hb caps" class:on={online[cam.id]}>{online[cam.id] ? '● ONLINE' : '○ OFFLINE'}</span>
              </div>
              <div class="meta caps">
                <span class="tab">/// {cam.name}</span>
                <span class="id">CAM {cam.id} · {cam.fps ? `${cam.fps.toFixed(0)} FPS` : '– FPS'}</span>
              </div>
              <div class="go caps">OBSERVE ▶</div>
            </button>
          {/each}
        </div>
      {/if}
    </div>
  {/if}

  <footer class="caps">
    <button class="alt" onclick={() => choose(null)}>CONTINUE WITHOUT SOURCE ▸</button>
    <button class="alt" onclick={toMap}>◈ MAP VIEW ▸</button>
  </footer>
</div>

<style>
  .select { position: fixed; inset: 0; z-index: var(--z-boot); background: radial-gradient(ellipse at 50% 30%, #0c0e12 0%, #000 80%);
    display: flex; flex-direction: column; padding: 5vh 5vw; overflow: hidden;
    transition: opacity 300ms var(--ease), filter 300ms var(--ease); animation: fadein 300ms var(--ease); }
  .select.leaving { opacity: 0; filter: blur(6px); }
  @keyframes fadein { from { opacity: 0; } }

  .corner { position: absolute; width: 30px; height: 30px; border: 2px solid var(--ink); opacity: 0.5; }
  .tl { top: 20px; left: 20px; border-right: 0; border-bottom: 0; }
  .tr { top: 20px; right: 20px; border-left: 0; border-bottom: 0; }
  .bl { bottom: 20px; left: 20px; border-right: 0; border-top: 0; }
  .br { bottom: 20px; right: 20px; border-left: 0; border-top: 0; }

  header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }
  .title { font-family: var(--font-display); font-weight: 700; font-size: 26px; letter-spacing: var(--tracking-wide); color: var(--ink); }
  .sub { font-size: var(--fs-label); color: var(--ink-dim); margin-top: 6px; }
  .cur::after { content: '\2588'; margin-left: 2px; animation: blink 1s steps(2) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .mng { padding: 7px 14px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .mng:hover, .mng.on { border-color: var(--scarlet); color: var(--scarlet); }

  /* centered stage — grid has an explicit width so auto-fill makes real columns */
  .stage { flex: 1; min-height: 0; overflow: auto; display: flex; }
  .grid { margin: auto; width: min(900px, 100%); display: grid;
    grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 16px; padding: 10px; }

  .card { text-align: left; cursor: crosshair; overflow: hidden; padding: 0; animation: cardin 360ms var(--ease) both; }
  @keyframes cardin { from { opacity: 0; transform: translateY(14px) scale(0.98); } }
  .card:hover { border-color: var(--scarlet); box-shadow: 0 0 20px rgba(225,6,0,0.18); }
  .card.off { opacity: 0.72; }

  .thumb { position: relative; aspect-ratio: 16 / 9; background: #05070a; overflow: hidden; }
  .thumb .ret { position: absolute; left: 50%; top: 50%; width: 42px; height: 42px; transform: translate(-50%, -50%); z-index: 2;
    border: 1px solid rgba(236,236,236,0.35); border-radius: 50%; pointer-events: none; }
  .thumb .ret::after { content: ''; position: absolute; inset: 13px; border: 1px solid rgba(236,236,236,0.25); border-radius: 50%; }
  .hb { position: absolute; left: 7px; bottom: 6px; font-size: 8px; color: var(--ink-ghost); z-index: 2; text-shadow: 0 0 4px #000; }
  .hb.on { color: var(--ink); }

  .meta { display: flex; flex-direction: column; gap: 3px; padding: 8px 10px; }
  .meta .tab { font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink); }
  .card:hover .meta .tab { color: var(--scarlet); }
  .meta .id { font-size: var(--fs-micro); color: var(--ink-ghost); }
  .go { padding: 6px 10px; border-top: 1px solid var(--hairline); font-size: var(--fs-micro); color: var(--ink-dim); letter-spacing: var(--tracking); }
  .card:hover .go { color: var(--scarlet); }

  .mtempty { margin: auto; text-align: center; color: var(--ink-dim); font-size: var(--fs-title); letter-spacing: var(--tracking); }
  .mtempty span { font-size: var(--fs-micro); color: var(--ink-ghost); }

  /* manage panel */
  .mgr { margin: auto; width: min(560px, 90vw); padding: 12px 14px; }
  .mgr .ph { font-size: var(--fs-label); color: var(--scarlet); letter-spacing: var(--tracking); margin: 6px 0; }
  .form { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
  .form label { display: flex; flex-direction: column; gap: 3px; font-size: 8px; color: var(--ink-ghost); }
  .form input { background: #000; border: 1px solid var(--hairline); color: var(--ink); font-family: var(--font-mono); font-size: var(--fs-micro); padding: 6px 8px; }
  .form input:focus { outline: none; border-color: var(--scarlet); }
  .uhint { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.12em; margin-top: -3px; }
  .fbtn { display: flex; gap: 8px; }
  .b { padding: 6px 16px; border: 1px solid var(--ink-dim); font-size: var(--fs-micro); letter-spacing: var(--tracking); color: var(--ink-dim); }
  .b:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .b:disabled { opacity: 0.5; cursor: default; }
  .rows { display: flex; flex-direction: column; gap: 3px; max-height: 34vh; overflow-y: auto; }
  .row { display: flex; align-items: center; gap: 8px; padding: 4px 2px; font-size: var(--fs-micro); border-bottom: 1px solid var(--hairline); }
  .row .dot { width: 7px; height: 7px; border: 1px solid var(--ink-ghost); }
  .row .dot.live { background: var(--cyan); border-color: var(--cyan); }
  .row .nm { flex: 1; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mini { padding: 2px 8px; border: 1px solid var(--ink-ghost); font-size: 8px; letter-spacing: 0.08em; color: var(--ink-dim); }
  .mini:hover { border-color: var(--ink); color: var(--ink); }
  .mini.del:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .empty { color: var(--ink-ghost); font-size: var(--fs-micro); padding: 8px; }

  footer { display: flex; gap: 16px; margin-top: 18px; }
  .alt { padding: 8px 16px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .alt:hover { border-color: var(--ink); color: var(--ink); }
</style>
