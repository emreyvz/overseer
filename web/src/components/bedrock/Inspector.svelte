<script lang="ts">
  // BEDROCK — the inspector.
  //
  // A fact without its provenance is a rumour, so every claim here carries which model asserted
  // it, from which frame, with what confidence, and when we came to believe it. Beliefs the
  // system has since retracted stay visible with a line through them: a correction never
  // deletes, and an operator must be able to see what was thought at the time.
  import { predMeta, vocab } from '../../lib/bedrock'
  import { api } from '../../lib/api'
  import { sfx } from '../../lib/audio'
  import { SIM } from '../../lib/sim'
  import type { BedrockEntity, BedrockFact } from '../../lib/types'

  let { entity, fact, onpurge, onclose }: {
    entity: BedrockEntity | null
    fact: BedrockFact | null
    onpurge: (uid: number) => void
    onclose: () => void
  } = $props()

  let history = $state<BedrockFact[]>([])
  let current = $state<BedrockFact[]>([])
  let lineage = $state<BedrockFact[]>([])
  let loading = $state(false)
  let holdT = $state(0)
  let holdTimer: ReturnType<typeof setInterval> | undefined

  const v = $derived($vocab)
  const label = (f: BedrockFact) =>
    `${(predMeta(v, f.pred)?.label ?? f.pred).toUpperCase()}${f.val ? ` ${f.val}` : ''}`
  const stamp = (ms: number | null) => ms === null ? 'STILL TRUE'
    : new Date(ms).toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' })

  $effect(() => {
    const e = entity
    if (!e) { history = []; current = []; return }
    if (SIM) { current = []; history = []; return }
    loading = true
    api.bedrockEntity(e.uid)
      .then((r) => { current = r.current ?? []; history = r.history ?? [] })
      .catch(() => { current = []; history = [] })
      .finally(() => (loading = false))
  })

  $effect(() => {
    const f = fact
    if (!f || SIM) { lineage = []; return }
    api.bedrockProvenance(f.id).then((r) => (lineage = r.lineage ?? [])).catch(() => (lineage = []))
  })

  function startHold(uid: number) {
    sfx('glitch')
    holdT = 0
    holdTimer = setInterval(() => {
      holdT += 60
      if (holdT >= 1200) { clearInterval(holdTimer); onpurge(uid); holdT = 0 }
    }, 60)
  }
  function endHold() { clearInterval(holdTimer); holdT = 0 }
</script>

<aside class="insp">
  {#if fact}
    <header class="ih caps">
      <span class="ik">PROVENANCE</span>
      <button class="x" onclick={onclose} aria-label="close">✕</button>
    </header>
    {#if fact.snapshot}
      <img class="shot" src={fact.snapshot} alt="" />
    {:else}
      <div class="shot ph caps">NO FRAME KEPT FOR THIS FACT</div>
    {/if}
    <div class="claim caps">{label(fact)}</div>
    <dl class="prov">
      <div><dt class="caps">ASSERTED BY</dt><dd>{fact.src_kind}{fact.model_id ? ` · ${fact.model_id}` : ''}</dd></div>
      <div><dt class="caps">CONFIDENCE</dt>
        <dd><span class="cbar"><span class="cfill" style={`width:${Math.round(fact.conf * 100)}%`}></span></span>
          {(fact.conf * 100).toFixed(0)}%</dd></div>
      <div><dt class="caps">SOURCE</dt><dd>{fact.src_ref ?? '—'}</dd></div>
      <div><dt class="caps">TRUE FROM</dt><dd>{stamp(fact.valid_from)}</dd></div>
      <div><dt class="caps">TRUE UNTIL</dt><dd>{stamp(fact.valid_to)}</dd></div>
      <div><dt class="caps">BELIEVED SINCE</dt><dd>{stamp(fact.tx_from)}</dd></div>
      {#if fact.tx_to !== null}
        <div><dt class="caps amb">RETRACTED</dt><dd class="amb">{stamp(fact.tx_to)}</dd></div>
      {/if}
    </dl>
    {#if lineage.length}
      <div class="ik caps">THIS REPLACED</div>
      <div class="lin">
        {#each lineage as l (l.id)}
          <div class="lrow caps"><span class="strike">{label(l)}</span>
            <span class="lmeta">{l.src_kind} · {(l.conf * 100).toFixed(0)}%</span></div>
        {/each}
      </div>
    {/if}
  {:else if entity}
    <header class="ih caps">
      <span class="ik">{entity.kind}</span>
      <button class="x" onclick={onclose} aria-label="close">✕</button>
    </header>
    {#if entity.snapshot}<img class="shot" src={entity.snapshot} alt="" />{/if}
    <div class="claim">{entity.label || entity.ref}</div>
    <div class="sub caps">{entity.ref}</div>
    <dl class="prov">
      <div><dt class="caps">FIRST SEEN</dt><dd>{stamp(entity.first_seen)}</dd></div>
      <div><dt class="caps">LAST SEEN</dt><dd>{stamp(entity.last_seen)}</dd></div>
    </dl>

    <div class="ik caps">CURRENT BELIEFS{loading ? ' ·' : ''}</div>
    <div class="facts">
      {#each current as f (f.id)}
        <div class="frow"><span class="fp caps">{label(f)}</span>
          <span class="fc">{(f.conf * 100).toFixed(0)}%</span></div>
      {:else}
        <div class="none caps">{loading ? 'READING_' : 'NOTHING CURRENTLY ASSERTED'}</div>
      {/each}
    </div>

    {#if history.some((f) => f.tx_to !== null)}
      <div class="ik caps">BELIEF LOG</div>
      <div class="facts">
        {#each history.filter((f) => f.tx_to !== null) as f (f.id)}
          <div class="frow"><span class="fp caps strike">{label(f)}</span>
            <span class="fc amb">RETRACTED</span></div>
        {/each}
      </div>
    {/if}

    <div class="acts">
      <button class="purge caps" onpointerdown={() => startHold(entity.uid)}
        onpointerup={endHold} onpointerleave={endHold}>
        <span class="pfill" style={`width:${(holdT / 1200) * 100}%`}></span>
        <span class="ptxt">⌫ PURGE THIS SUBJECT</span>
      </button>
      <div class="pnote caps">
        HOLD TO CONFIRM · IRREVERSIBLE · DESTROYS EVERY FACT AND SNAPSHOT FOR THIS INDIVIDUAL
      </div>
    </div>
  {:else}
    <div class="none caps pad">SELECT AN ENTITY OR A FACT</div>
  {/if}
</aside>

<style>
  .insp { display: flex; flex-direction: column; gap: 8px; padding: 14px 12px 30px; overflow-y: auto;
    border-left: 1px solid var(--hairline); background: rgba(4,7,10,0.4); }
  .ih { display: flex; align-items: center; gap: 8px; }
  .ik { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.18em; }
  .x { margin-left: auto; background: none; border: none; color: var(--ink-ghost); font-size: 10px; cursor: crosshair; }
  .x:hover { color: var(--scarlet); }
  .shot { width: 100%; aspect-ratio: 16/9; object-fit: cover; border: 1px solid var(--hairline); }
  .shot.ph { display: flex; align-items: center; justify-content: center; color: var(--ink-ghost);
    font-size: 8px; letter-spacing: 0.12em; background: repeating-linear-gradient(45deg, #0a0d12 0 8px, #070a0e 8px 16px); }
  .claim { font-size: 13px; color: var(--ink); letter-spacing: 0.04em; line-height: 1.4; }
  .sub { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  .prov { display: flex; flex-direction: column; gap: 4px; margin: 0; }
  .prov > div { display: grid; grid-template-columns: 92px 1fr; gap: 8px; align-items: baseline; }
  .prov dt { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.14em; }
  .prov dd { margin: 0; font-size: 9px; color: var(--ink-dim); display: flex; align-items: center; gap: 6px; }
  .amb { color: var(--amber) !important; }
  .cbar { position: relative; width: 54px; height: 3px; background: var(--hairline); }
  .cfill { position: absolute; inset: 0 auto 0 0; background: var(--cyan); }
  .facts { display: flex; flex-direction: column; gap: 3px; }
  .frow { display: flex; align-items: baseline; gap: 8px; font-size: 9px; }
  .fp { color: var(--ink-dim); letter-spacing: 0.08em; flex: 1; line-height: 1.5; }
  .fc { color: var(--ink-ghost); font-size: 8px; }
  .strike { text-decoration: line-through; color: var(--ink-ghost) !important; }
  .lin { display: flex; flex-direction: column; gap: 4px; }
  .lrow { display: flex; flex-direction: column; gap: 1px; font-size: 9px; }
  .lmeta { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  .none { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.12em; }
  .none.pad { padding: 30px 6px; text-align: center; }
  .acts { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
  .purge { position: relative; overflow: hidden; padding: 9px 0; border: 1px solid var(--ink-dim);
    background: none; color: var(--ink-dim); font-size: 9px; letter-spacing: 0.14em; cursor: crosshair; }
  .purge:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .pfill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--scarlet); opacity: 0.28; }
  .ptxt { position: relative; }
  .pnote { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.1em; line-height: 1.6; }
</style>
