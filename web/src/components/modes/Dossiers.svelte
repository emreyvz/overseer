<script lang="ts">
  // Long-term identity dossiers (features 5/6/7): the persisted subjects recognized across days,
  // each with its repeat-visit pattern, gait / soft-biometric profile, and an on-demand multi-frame
  // super-resolution reconstruction of the subject from its sighting crops.
  import { onMount, onDestroy } from 'svelte'
  import { api, type Subject, type Dossier, type Reconstruction } from '../../lib/api'
  import { mode } from '../../lib/stores'

  const API = api.base
  let subjects = $state<Subject[]>([])
  let selected = $state<Dossier | null>(null)
  let selId = $state<number | null>(null)
  let recon = $state<Reconstruction | null>(null)
  let reconBusy = $state(false)
  let timer: ReturnType<typeof setInterval> | null = null

  const img = (s?: string | null) => (s ? API + s : '')
  const dt = (ts: number) => new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  const num = (v: unknown) => (typeof v === 'number' ? Math.round(v * 100) / 100 : v)

  async function refresh() {
    try { subjects = await api.subjects() } catch { /* offline */ }
  }
  async function open(id: number) {
    selId = id; recon = null
    try { selected = (await api.subjectDossier(id)).dossier } catch { selected = null }
  }
  async function reconstruct() {
    if (selId == null || reconBusy) return
    reconBusy = true; recon = null
    try { recon = await api.subjectReconstruct(selId) } catch { recon = { image: null, reason: 'error' } }
    reconBusy = false
  }

  onMount(() => { refresh(); timer = setInterval(refresh, 3000) })
  onDestroy(() => { if (timer) clearInterval(timer) })

  const maxHour = $derived(selected ? Math.max(1, ...selected.hour_histogram) : 1)
  const maxCam = $derived(selected ? Math.max(1, ...selected.per_camera.map((c) => c.count)) : 1)
</script>

<div class="wrap">
  <header>
    <span class="ttl">IDENTITY DOSSIERS</span>
    <span class="sub">{subjects.length} tracked subjects · recognized across days</span>
    <button class="x" onclick={() => mode.set('pov')}>ESC</button>
  </header>

  <div class="body">
    <div class="grid">
      {#each subjects as s (s.id)}
        <button class="card" class:sel={s.id === selId} onclick={() => open(s.id)}>
          {#if s.snapshot}<img src={img(s.snapshot)} alt="" />{:else}<div class="ph">{s.cls === 'vehicle' ? '🚗' : '👤'}</div>{/if}
          <div class="meta">
            <span class="id">{s.label || (s.cls === 'vehicle' ? 'V' : 'P') + '-' + s.id}</span>
            <span class="vc">{s.day_count}d · {s.sighting_count}×</span>
          </div>
          {#if s.flags.includes('repeat_visitor')}<span class="flag">REPEAT</span>{/if}
        </button>
      {:else}
        <p class="empty">No long-term subjects yet. As people and vehicles are seen across days they appear here.</p>
      {/each}
    </div>

    {#if selected}
      <div class="detail">
        <div class="hero">
          {#if recon?.image}
            <img class="over" src={'data:image/jpeg;base64,' + recon.image} alt="reconstruction" />
          {:else if selected.snapshot}
            <img class="over" src={img(selected.snapshot)} alt="" />
          {:else}
            <div class="over ph">{selected.cls === 'vehicle' ? '🚗' : '👤'}</div>
          {/if}
          <button class="recon" onclick={reconstruct} disabled={reconBusy}>
            {reconBusy ? 'FUSING…' : recon?.image ? '✓ SUPER-RES' : 'RECONSTRUCT'}
          </button>
          {#if recon && !recon.image}<span class="reconmsg">{recon.reason === 'not_enough_frames' ? `need more crops (${recon.frames_offered ?? 0})` : recon.reason}</span>{/if}
          {#if recon?.image}<span class="recongain">{recon.method === 'multiframe' ? `fused ${recon.frames_used} frames` : 'enhanced (need consecutive crops to fuse)'}</span>{/if}
        </div>

        <div class="info">
          <div class="head">
            <span class="sid">{selected.label || (selected.cls === 'vehicle' ? 'V' : 'P') + '-' + selected.id}</span>
            {#each selected.flags as f}<span class="fl">{f.replace('_', ' ')}</span>{/each}
          </div>
          <div class="stats">
            <div><b>{selected.distinct_days}</b><span>days seen</span></div>
            <div><b>{selected.sighting_count}</b><span>sightings</span></div>
            <div><b>{selected.per_camera.length}</b><span>cameras</span></div>
            <div><b>{dt(selected.first_seen)}</b><span>first seen</span></div>
            <div><b>{dt(selected.last_seen)}</b><span>last seen</span></div>
          </div>

          {#if selected.attrs?.gait || selected.attrs?.cadence_hz}
            <div class="sec">GAIT · SOFT BIOMETRICS</div>
            <div class="bio">
              {#if selected.attrs.cadence_hz}<span>cadence <b>{num(selected.attrs.cadence_hz)} Hz</b></span>{/if}
              {#if selected.attrs.build_ratio}<span>build <b>{num(selected.attrs.build_ratio)}</b></span>{/if}
              {#if selected.attrs.leg_ratio}<span>leg/torso <b>{num(selected.attrs.leg_ratio)}</b></span>{/if}
            </div>
          {/if}

          <div class="sec">WHERE · per camera</div>
          <div class="cams">
            {#each selected.per_camera as c}
              <div class="bar"><span class="cn">{c.cam}</span><span class="track"><span class="fill" style="width:{(c.count / maxCam) * 100}%"></span></span><span class="cc">{c.count}</span></div>
            {/each}
          </div>

          <div class="sec">WHEN · hour of day</div>
          <div class="hours">
            {#each selected.hour_histogram as v, h}
              <span class="hb" title="{h}:00 — {v}" style="height:{6 + (v / maxHour) * 34}px" class:hot={v > 0}></span>
            {/each}
          </div>

          <div class="sec">SIGHTINGS</div>
          <div class="sights">
            {#each selected.sightings.slice(0, 40) as si (si.id)}
              <div class="sg">{#if si.snapshot}<img src={img(si.snapshot)} alt="" />{/if}<span>{si.cam || '?'} · {dt(si.ts)}</span></div>
            {/each}
          </div>
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .wrap { position: fixed; inset: 0; z-index: 60; background: #05080d; color: #dfe9f2; display: flex; flex-direction: column; font: 13px/1.4 system-ui, sans-serif }
  header { display: flex; align-items: center; gap: 14px; padding: 14px 20px; border-bottom: 1px solid #14202c }
  .ttl { color: #35e0ff; letter-spacing: .22em; font-weight: 700 }
  .sub { color: #6d8298; font-size: 12px }
  .x { margin-left: auto; background: #10202c; color: #9fb6c9; border: 1px solid #1d3040; border-radius: 6px; padding: 5px 12px; cursor: pointer; letter-spacing: .1em }
  .body { flex: 1; display: grid; grid-template-columns: 420px 1fr; overflow: hidden }
  .grid { overflow-y: auto; padding: 14px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; align-content: start; border-right: 1px solid #14202c }
  .card { position: relative; background: #0b141d; border: 1px solid #16242f; border-radius: 8px; overflow: hidden; cursor: pointer; padding: 0; text-align: left }
  .card.sel { border-color: #35e0ff; box-shadow: 0 0 0 1px #35e0ff55 }
  .card img { width: 100%; height: 96px; object-fit: cover; display: block }
  .card .ph { height: 96px; display: grid; place-items: center; font-size: 30px; background: #0e1a24 }
  .meta { display: flex; justify-content: space-between; padding: 5px 7px; font-size: 11px }
  .meta .id { color: #cfe0ee; font-weight: 600 } .meta .vc { color: #6d8298 }
  .flag { position: absolute; top: 6px; left: 6px; background: #ffb03822; color: #ffcf87; border: 1px solid #ffb03855; border-radius: 4px; font-size: 9px; padding: 1px 5px; letter-spacing: .1em }
  .empty { grid-column: 1/-1; color: #6d8298; padding: 24px; text-align: center }
  .detail { overflow-y: auto; display: grid; grid-template-columns: 320px 1fr; gap: 18px; padding: 18px }
  .hero { position: relative }
  .hero .over { width: 100%; border-radius: 10px; display: block; background: #0b141d; image-rendering: auto }
  .hero .ph { height: 300px; display: grid; place-items: center; font-size: 60px }
  .recon { position: absolute; bottom: 10px; left: 10px; right: 10px; background: #35e0ff; color: #052027; border: 0; border-radius: 7px; padding: 8px; font-weight: 700; letter-spacing: .12em; cursor: pointer }
  .recon:disabled { opacity: .6 }
  .reconmsg, .recongain { display: block; margin-top: 8px; font-size: 11px; color: #6d8298; text-align: center }
  .recongain { color: #6be675 }
  .info .head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px }
  .sid { font-size: 20px; font-weight: 700; letter-spacing: .04em }
  .fl { background: #ffb03822; color: #ffcf87; border: 1px solid #ffb03855; border-radius: 5px; font-size: 10px; padding: 2px 7px; letter-spacing: .1em; text-transform: uppercase }
  .stats { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 6px }
  .stats div { display: flex; flex-direction: column } .stats b { color: #35e0ff; font-size: 15px } .stats span { color: #6d8298; font-size: 11px }
  .sec { color: #4f6579; letter-spacing: .18em; font-size: 10px; margin: 16px 0 8px; border-top: 1px solid #14202c; padding-top: 10px }
  .bio { display: flex; gap: 16px; flex-wrap: wrap } .bio span { color: #9fb6c9 } .bio b { color: #cfe0ee }
  .cams .bar { display: flex; align-items: center; gap: 8px; margin: 4px 0; font-size: 12px }
  .cams .cn { width: 90px; color: #9fb6c9; overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
  .cams .track { flex: 1; height: 8px; background: #0e1a24; border-radius: 4px; overflow: hidden }
  .cams .fill { display: block; height: 100%; background: #35e0ff }
  .cams .cc { width: 30px; text-align: right; color: #6d8298 }
  .hours { display: flex; align-items: flex-end; gap: 3px; height: 44px }
  .hb { flex: 1; background: #16242f; border-radius: 2px } .hb.hot { background: #35e0ff }
  .sights { display: flex; flex-wrap: wrap; gap: 8px }
  .sg { width: 92px; font-size: 10px; color: #6d8298 } .sg img { width: 92px; height: 64px; object-fit: cover; border-radius: 5px; display: block; margin-bottom: 3px }
</style>
