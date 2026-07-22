<script lang="ts">
  // Enroll a detection straight from the live feed: capture its cropped image,
  // classify kind + threat with icons, name it → saved to the watchlist so it can
  // be found later. Appearance-based, session-local (OSINT-legal).
  import { get } from 'svelte/store'
  import { enrollOpen, activeCam, cameras, flashBanner } from '../../lib/stores'
  import { enroll, type EntityKind, type ThreatLevel } from '../../lib/watchlist'
  import { sfx } from '../../lib/audio'
  import { onMount } from 'svelte'
  import type { Detection } from '../../lib/types'

  const SNAP = (import.meta.env.VITE_SNAP_BASE as string | undefined) ?? 'http://127.0.0.1:8787/snap'
  const det = get(enrollOpen) as Detection
  const camId = get(activeCam)
  const cam = get(cameras).find((c) => c.id === camId)

  const KINDS: { k: EntityKind; icon: string; label: string }[] = [
    { k: 'person', icon: '👤', label: 'PERSON' },
    { k: 'vehicle', icon: '🚗', label: 'VEHICLE' },
    { k: 'pet', icon: '🐾', label: 'PET / ANIMAL' },
    { k: 'object', icon: '⬢', label: 'OBJECT' },
  ]
  const THREATS: { t: ThreatLevel; icon: string; label: string }[] = [
    { t: 'safe', icon: '◇', label: 'SAFE' },
    { t: 'watch', icon: '△', label: 'WATCH' },
    { t: 'threat', icon: '▲', label: 'THREAT' },
  ]

  const clsKind = (c: string): EntityKind => (c === 'animal' ? 'pet' : c === 'vehicle' ? 'vehicle' : c === 'object' ? 'object' : 'person')
  let kind = $state<EntityKind>(clsKind(det?.cls ?? 'person'))
  let threat = $state<ThreatLevel>('watch')
  let name = $state('')
  let notes = $state('')
  let image = $state('')

  // Capture the detection crop from the camera snapshot.
  onMount(() => {
    if (!camId) return
    const im = new Image()
    im.crossOrigin = 'anonymous'
    im.onload = () => {
      try {
        const [bx, by, bw, bh] = det.bbox
        const pad = 0.18
        const sx = Math.max(0, (bx - bw * pad) * im.width)
        const sy = Math.max(0, (by - bh * pad) * im.height)
        const sw = Math.min(im.width - sx, bw * (1 + 2 * pad) * im.width)
        const sh = Math.min(im.height - sy, bh * (1 + 2 * pad) * im.height)
        const cv = document.createElement('canvas')
        const scale = 200 / Math.max(1, sw)
        cv.width = Math.round(sw * scale); cv.height = Math.round(sh * scale)
        cv.getContext('2d')?.drawImage(im, sx, sy, sw, sh, 0, 0, cv.width, cv.height)
        image = cv.toDataURL('image/jpeg', 0.72)
      } catch {
        image = `${SNAP}/${camId}` // cross-origin taint fallback: reference the live snap
      }
    }
    im.onerror = () => { image = '' }
    im.src = `${SNAP}/${camId}?t=${Date.now()}`
  })

  function save() {
    sfx('sonar')
    enroll({
      kind, name: name.trim() || KINDS.find((x) => x.k === kind)!.label, threat, image,
      cam: cam?.name, camId: camId ?? undefined,
      color: det?.attrs?.upper_color, height: det?.attrs?.height,
    })
    flashBanner('ENROLLED TO WATCHLIST', false, 1400)
    enrollOpen.set(null)
  }
  function close() { sfx('click'); enrollOpen.set(null) }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="scrim" onclick={close} role="presentation">
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" tabindex="-1">
    <div class="hdr caps"><span class="hot">⊕</span> ENROLL TARGET <button class="x" onclick={close} aria-label="close">×</button></div>
    <div class="body">
      <div class="cap">
        {#if image}<img src={image} alt="" />{:else}<div class="cap-empty caps">CAPTURING…</div>{/if}
        <div class="capmeta caps">{cam?.name ?? 'CAM —'}{#if det?.attrs?.upper_color} · {det.attrs.upper_color.toUpperCase()}{/if}</div>
      </div>
      <div class="form">
        <div class="gl caps">KIND</div>
        <div class="chips">
          {#each KINDS as o}
            <button class="chip caps" class:on={kind === o.k} onclick={() => { kind = o.k; sfx('click', { volume: 0.2 }) }}>
              <span class="ic">{o.icon}</span> {o.label}
            </button>
          {/each}
        </div>
        <div class="gl caps">THREAT LEVEL</div>
        <div class="chips">
          {#each THREATS as o}
            <button class="chip caps thr-{o.t}" class:on={threat === o.t} onclick={() => { threat = o.t; sfx('click', { volume: 0.2 }) }}>
              <span class="ic">{o.icon}</span> {o.label}
            </button>
          {/each}
        </div>
        <label class="fld caps">NAME<input bind:value={name} placeholder="e.g. LUNA / SUSPECT-1" spellcheck="false" /></label>
        <label class="fld caps">NOTES<input bind:value={notes} placeholder="OPTIONAL" spellcheck="false" /></label>
        <div class="btns">
          <button class="go caps" onclick={save}>⊕ ENROLL</button>
          <button class="cancel caps" onclick={close}>CANCEL</button>
        </div>
      </div>
    </div>
  </div>
</div>

<style>
  .scrim { position: absolute; inset: 0; z-index: var(--z-cmd); background: rgba(2,3,4,0.72); display: grid; place-items: center; animation: fin 180ms; }
  @keyframes fin { from { opacity: 0; } }
  .modal { width: min(560px, 92vw); background: #070809; border: 1px solid var(--ink); box-shadow: 0 0 40px rgba(0,0,0,0.7); animation: up 200ms var(--ease); }
  @keyframes up { from { transform: translateY(12px); opacity: 0; } }
  .hdr { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-bottom: 1px solid var(--hairline); font-size: var(--fs-title); letter-spacing: var(--tracking-wide); color: var(--ink); }
  .hdr .hot { color: var(--scarlet); }
  .hdr .x { margin-left: auto; font-size: 16px; color: var(--ink-dim); cursor: pointer; } .hdr .x:hover { color: var(--scarlet); }
  .body { display: grid; grid-template-columns: 180px 1fr; gap: 14px; padding: 14px; }
  .cap { display: flex; flex-direction: column; gap: 6px; }
  .cap img { width: 100%; aspect-ratio: 3/4; object-fit: cover; border: 1px solid var(--ink); background: #05070a; }
  .cap-empty { width: 100%; aspect-ratio: 3/4; display: grid; place-content: center; border: 1px solid var(--hairline); color: var(--ink-ghost); font-size: var(--fs-micro); }
  .capmeta { font-size: var(--fs-micro); color: var(--ink-dim); letter-spacing: 0.08em; }
  .form { display: flex; flex-direction: column; gap: 6px; }
  .gl { font-size: var(--fs-micro); color: var(--ink-ghost); letter-spacing: var(--tracking); margin-top: 4px; }
  .chips { display: flex; flex-wrap: wrap; gap: 5px; }
  .chip { display: inline-flex; align-items: center; gap: 5px; padding: 5px 9px; border: 1px solid var(--hairline); font-size: var(--fs-micro); letter-spacing: 0.08em; color: var(--ink-dim); cursor: pointer; }
  .chip .ic { font-size: 13px; filter: grayscale(0.2); }
  .chip:hover { border-color: var(--ink-dim); color: var(--ink); }
  .chip.on { background: var(--ink); color: var(--scarlet-ink); border-color: var(--ink); }
  .chip.thr-safe.on { background: var(--cyan); border-color: var(--cyan); color: #04070a; }
  .chip.thr-watch.on { background: #d8a200; border-color: #d8a200; color: #000; }
  .chip.thr-threat.on { background: var(--scarlet); border-color: var(--scarlet); color: #fff; }
  .fld { display: flex; flex-direction: column; gap: 3px; font-size: 8px; color: var(--ink-ghost); margin-top: 3px; }
  .fld input { background: #000; border: 1px solid var(--hairline); color: var(--ink); font-family: var(--font-mono); font-size: var(--fs-micro); padding: 6px 8px; }
  .fld input:focus { outline: none; border-color: var(--scarlet); }
  .btns { display: flex; gap: 8px; margin-top: 8px; }
  .go { flex: 1; padding: 8px; border: 1px solid var(--scarlet); color: var(--scarlet); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .go:hover { background: var(--scarlet); color: #fff; }
  .cancel { padding: 8px 14px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .cancel:hover { border-color: var(--ink); color: var(--ink); }
</style>
