<script lang="ts">
  // Overseer-style annotated replay: draws where to look on an incident clip — a
  // target bracket on the triggering object (person/vehicle/weapon/abandoned object),
  // the breached zone outline, and the AI 'why this fired' caption. Overlays the video.
  import type { AlertMark } from '../lib/types'

  let { mark, label, threat, reason, alarm = false }:
    { mark?: AlertMark; label: string; threat?: string; reason?: string; alarm?: boolean } = $props()

  const KIND_ICON: Record<string, string> = {
    person: '◉', vehicle: '▤', object: '◫', weapon: '⚠', animal: '✦',
  }
  // bbox as CSS percentages for the floating card (normalized x,y,w,h → %).
  let box = $derived(mark?.bbox
    ? { l: mark.bbox[0] * 100, t: mark.bbox[1] * 100, w: mark.bbox[2] * 100, h: mark.bbox[3] * 100 }
    : null)
  let flip = $derived(box ? box.l + box.w > 62 : false)
  let icon = $derived(KIND_ICON[mark?.kind ?? 'object'] ?? '◫')
  let zonePts = $derived(mark?.zone?.map((p) => `${p[0]},${p[1]}`).join(' ') ?? '')
</script>

<div class="ov" class:alarm>
  <svg class="vec" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
    {#if zonePts}
      <polygon class="zone" points={zonePts} />
    {/if}
    {#if box}
      {@const x = box.l / 100}{@const y = box.t / 100}{@const w = box.w / 100}{@const h = box.h / 100}
      {@const c = 0.028}
      <!-- four corner brackets around the target -->
      <path class="cnr" d="M{x} {y + c} L{x} {y} L{x + c} {y}" />
      <path class="cnr" d="M{x + w - c} {y} L{x + w} {y} L{x + w} {y + c}" />
      <path class="cnr" d="M{x} {y + h - c} L{x} {y + h} L{x + c} {y + h}" />
      <path class="cnr" d="M{x + w - c} {y + h} L{x + w} {y + h} L{x + w} {y + h - c}" />
    {/if}
  </svg>

  {#if box}
    <div class="tgt" style={`left:${box.l}%;top:${box.t}%;width:${box.w}%;height:${box.h}%`}>
      <div class="card caps" class:flip>
        <div class="ctab"><span class="ci">{icon}</span> {label}</div>
        {#if threat}<div class="crow"><span class="ck">THREAT</span><span class="cv">{threat}</span></div>{/if}
        <div class="crow"><span class="ck">TRIGGER</span><span class="cv">{mark?.kind?.toUpperCase() ?? '—'}</span></div>
      </div>
    </div>
  {/if}

  <div class="cap caps">
    <span class="chot">◇ WHY</span>
    {#if reason}<span class="ctx">{reason}</span>{:else}<span class="cwait">ANALYZE THE INCIDENT FOR AN EXPLANATION</span>{/if}
  </div>
</div>

<style>
  .ov { position: absolute; inset: 0; pointer-events: none; font-family: var(--font-mono); }
  .vec { position: absolute; inset: 0; width: 100%; height: 100%; }
  .vec .cnr { fill: none; stroke: var(--ink); stroke-width: 2; vector-effect: non-scaling-stroke; }
  .ov.alarm .vec .cnr { stroke: var(--scarlet); filter: drop-shadow(0 0 3px var(--scarlet-glow)); }
  .vec .zone { fill: rgba(225,6,0,0.06); stroke: var(--scarlet); stroke-width: 1.5; stroke-dasharray: 5 4;
    vector-effect: non-scaling-stroke; filter: drop-shadow(0 0 4px var(--scarlet-glow)); animation: zdash 0.7s linear infinite; }
  @keyframes zdash { to { stroke-dashoffset: -9; } }

  .tgt { position: absolute; }
  .tgt::before { content: ''; position: absolute; inset: 0; border: 1px solid rgba(255,255,255,0.14); }
  .card { position: absolute; left: 100%; top: 0; margin-left: 6px; min-width: 118px; background: rgba(6,7,9,0.92);
    border: 1px solid var(--ink); padding: 4px 6px; display: flex; flex-direction: column; gap: 2px; animation: cin 220ms var(--ease); }
  .card.flip { left: auto; right: 100%; margin-left: 0; margin-right: 6px; }
  .ov.alarm .card { border-color: var(--scarlet); }
  @keyframes cin { from { opacity: 0; transform: translateX(-4px); } }
  .ctab { font-size: 9px; letter-spacing: 0.1em; color: var(--ink); font-weight: 700; }
  .ov.alarm .ctab { color: var(--scarlet); }
  .ctab .ci { margin-right: 3px; }
  .crow { display: flex; justify-content: space-between; gap: 10px; font-size: 8px; }
  .crow .ck { color: var(--ink-dim); } .crow .cv { color: var(--ink); }

  .cap { position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; gap: 8px;
    padding: 6px 10px; background: linear-gradient(0deg, rgba(3,4,5,0.9), transparent); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .cap .chot { color: var(--cyan); font-weight: 700; }
  .cap .ctx { color: var(--ink); line-height: 1.3; }
  .cap .cwait { color: var(--ink-ghost); }
</style>
