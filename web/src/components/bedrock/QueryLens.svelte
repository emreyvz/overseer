<script lang="ts">
  // BEDROCK — the lens.
  //
  // The query is chips, not text. The plain-language box compiles INTO the chips and they
  // assemble one by one, so the operator watches their sentence become a structured query and
  // corrects a wrong reading by editing one chip rather than re-prompting and hoping.
  import { WINDOWS, clauseText, predMeta, queryError, running, suggestions, vocab } from '../../lib/bedrock'
  import { api } from '../../lib/api'
  import { sfx } from '../../lib/audio'
  import { SIM } from '../../lib/sim'
  import type { BedrockClause, BedrockEntity, BedrockQuery } from '../../lib/types'

  let { query, entities = [], onrun, onchange }: {
    query: BedrockQuery
    entities?: BedrockEntity[]
    onrun: (q: BedrockQuery) => void
    onchange: (q: BedrockQuery) => void
  } = $props()

  let text = $state('')
  let thinking = $state(false)
  let assembling = $state(0)          // how many chips have landed, for the stagger
  let openPop = $state<string | null>(null)
  let filter = $state('')

  const v = $derived($vocab)
  const winLabel = $derived.by(() => {
    if (!query.window) return 'ALL'
    const span = query.window.to - query.window.from
    return WINDOWS.find(([, ms]) => Math.abs(ms - span) < ms * 0.1)?.[0]
      ?? `${Math.round(span / 3_600_000)}H`
  })

  function edit(fn: (q: BedrockQuery) => void) {
    const next: BedrockQuery = JSON.parse(JSON.stringify(query))
    fn(next)
    onchange(next)
  }
  function addClause() {
    edit((q) => q.where.push({ t: 'pred', pred: v?.predicates[0]?.pred ?? 'seen_on' }))
    sfx('click', { volume: 0.2 })
  }
  function removeClause(i: number) {
    edit((q) => { q.where.splice(i, 1) })
    sfx('click', { volume: 0.2 })
  }
  function setWindow(ms: number) {
    const now = Date.now()
    edit((q) => { q.window = { from: now - ms, to: now } })
    sfx('click', { volume: 0.2 })
  }
  function negate(i: number) {
    edit((q) => {
      const c = q.where[i]
      q.where[i] = c.t === 'not' ? c.clause : { t: 'not', clause: c }
    })
  }
  function toAllen(i: number) {
    edit((q) => {
      q.where[i] = { t: 'allen', rel: 'before', a: 0, b: Math.max(1, q.where.length - 1) }
    })
  }

  async function compile() {
    if (!text.trim()) return
    thinking = true
    assembling = 0
    sfx('sonar')
    try {
      const r = SIM
        ? { query: { ...query, where: [{ t: 'kind', kind: 'vehicle' }, { t: 'pred', pred: 'wore', val: 'white' }] } as BedrockQuery }
        : await api.aiBedrock(text)
      if (r.query) {
        onchange(r.query as BedrockQuery)
        // the chips land one at a time: this is the operator watching the AI's reading form
        for (let i = 0; i <= (r.query.where?.length ?? 0); i++) {
          assembling = i
          await new Promise((res) => setTimeout(res, 60))
        }
        onrun(r.query as BedrockQuery)
      }
    } catch { /* offline: the chips stay as they were */ }
    thinking = false
    assembling = 99
  }

  const PRED_LIST = $derived((v?.predicates ?? []).filter(
    (p) => !filter || p.label.toLowerCase().includes(filter.toLowerCase()) || p.pred.includes(filter.toLowerCase())))
</script>

<aside class="lens">
  <div class="lk caps">THE QUESTION</div>

  <div class="clauses">
    {#each query.where as c, i (i)}
      {@const parts = clauseText(v, c, entities)}
      <div class="row" class:neg={c.t === 'not'} class:landed={assembling > i}>
        <span class="idx caps">{i + 1}</span>
        <button class="chip caps" onclick={() => (openPop = openPop === `s${i}` ? null : `s${i}`)}>
          {parts[0] || 'ANY'}
        </button>
        <button class="chip mid caps" onclick={() => (openPop = openPop === `p${i}` ? null : `p${i}`)}>
          {parts[1]}
        </button>
        <button class="chip caps" onclick={() => (openPop = openPop === `o${i}` ? null : `o${i}`)}>
          {parts[2] || 'ANYTHING'}
        </button>
        <button class="mini" onclick={() => negate(i)} title="Negate this clause">¬</button>
        <button class="mini" onclick={() => toAllen(i)} title="Make this an interval relation">⧗</button>
        <button class="mini x" onclick={() => removeClause(i)} title="Remove">✕</button>

        {#if openPop === `p${i}` && c.t === 'pred'}
          <div class="pop">
            <input class="pf" bind:value={filter} placeholder="FILTER_" />
            <div class="plist">
              {#each PRED_LIST as p (p.pred)}
                <button class="pitem caps" class:on={c.pred === p.pred}
                  onclick={() => { edit((q) => { (q.where[i] as { pred: string }).pred = p.pred }); openPop = null }}>
                  <span class="pfam f-{p.family}"></span>{p.label}
                </button>
              {/each}
            </div>
          </div>
        {/if}
        {#if openPop === `o${i}` && c.t === 'pred'}
          <div class="pop">
            <input class="pf" placeholder="VALUE_" value={String(c.val ?? '')}
              onchange={(e) => { const val = (e.currentTarget as HTMLInputElement).value
                edit((q) => { (q.where[i] as { val?: string }).val = val || undefined }); openPop = null }} />
            <div class="phint caps">
              {predMeta(v, c.pred)?.object === 'number' ? 'A NUMBER, OR TWO FOR A RANGE' : 'EXACT VALUE, BLANK FOR ANY'}
            </div>
          </div>
        {/if}
        {#if openPop === `s${i}`}
          <div class="pop">
            <div class="plist">
              {#each (v?.kinds ?? []) as k}
                <button class="pitem caps" onclick={() => { edit((q) => { q.where[i] = { t: 'kind', kind: k } }); openPop = null }}>{k}</button>
              {/each}
            </div>
          </div>
        {/if}

        {#if c.t === 'allen'}
          <!-- the relation drawn, not just named: two bars beat the word "overlaps" -->
          <svg class="rel" viewBox="0 0 60 18">
            <rect class="ra" x="4" y="3" width="26" height="4" />
            <rect class="rb" x={c.rel === 'before' ? 34 : c.rel === 'during' ? 2 : c.rel === 'overlaps' ? 20 : 30}
              y="11" width={c.rel === 'during' ? 56 : 26} height="4" />
          </svg>
        {/if}
      </div>
    {/each}
    <button class="add caps" onclick={addClause}>+ AND</button>
  </div>

  <div class="lk caps">WHEN</div>
  <div class="wins">
    {#each WINDOWS as [label, ms]}
      <button class="win caps" class:on={winLabel === label} onclick={() => setWindow(ms)}>{label}</button>
    {/each}
  </div>

  <div class="lk caps">OR JUST ASK</div>
  <div class="ask">
    <input class="askin" bind:value={text} placeholder="ASK IN PLAIN LANGUAGE_"
      onkeydown={(e) => { if (e.key === 'Enter') compile() }} />
    {#if thinking}<span class="think caps">READING_</span>{/if}
  </div>

  {#if $queryError}
    <div class="err caps">
      <span class="etxt">{$queryError.message}</span>
      {#if $queryError.hint === 'window'}
        <button class="efix caps" onclick={() => { setWindow(7 * 86_400_000); onrun(query) }}>⏱ LIMIT TO 7 DAYS</button>
      {/if}
    </div>
  {/if}

  <div class="acts">
    <button class="run caps" onclick={() => onrun(query)} disabled={$running}>
      {$running ? 'QUERYING_' : '▶ RUN'}
    </button>
    <button class="clr caps" onclick={() => onchange({ ...query, where: [] })}>⌫ CLEAR</button>
  </div>

  {#if $suggestions.length}
    <div class="lk caps">SAVED STARTS</div>
    <div class="sugs">
      {#each $suggestions as s}
        <button class="sug caps" onclick={() => { onchange(s.query); onrun(s.query) }}>
          <span class="stxt">{s.label}</span><span class="sn">{s.count}</span>
        </button>
      {/each}
    </div>
  {/if}
</aside>

<style>
  .lens { display: flex; flex-direction: column; gap: 8px; padding: 14px 12px 30px; overflow-y: auto;
    border-right: 1px solid var(--hairline); background: rgba(4,7,10,0.4); }
  .lk { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.18em; margin-top: 8px; }
  .lk:first-child { margin-top: 0; }
  .clauses { display: flex; flex-direction: column; gap: 5px; }
  .row { position: relative; display: flex; align-items: center; gap: 3px; flex-wrap: wrap;
    padding: 4px 4px 4px 2px; border-left: 2px solid transparent; }
  .row.neg { border-left-color: var(--scarlet); }
  .row.landed { animation: land 260ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes land { from { opacity: 0; transform: translateX(-8px); } }
  .idx { font-size: 7px; color: var(--ink-ghost); width: 10px; }
  .chip { padding: 4px 7px; border: 1px solid var(--hairline); background: none; color: var(--ink-dim);
    font-size: 9px; letter-spacing: 0.1em; cursor: crosshair; max-width: 130px; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
  .chip:hover { border-color: var(--cyan); color: var(--cyan); }
  .chip.mid { color: var(--ink); }
  .mini { width: 16px; height: 18px; border: none; background: none; color: var(--ink-ghost);
    font-size: 9px; cursor: crosshair; padding: 0; }
  .mini:hover { color: var(--ink); } .mini.x:hover { color: var(--scarlet); }
  .add { align-self: flex-start; margin-top: 2px; padding: 4px 8px; border: 1px dashed var(--hairline);
    background: none; color: var(--ink-ghost); font-size: 8px; letter-spacing: 0.14em; cursor: crosshair; }
  .add:hover { color: var(--cyan); border-color: var(--cyan); }

  .pop { position: absolute; left: 12px; top: 100%; z-index: 4; width: 220px; padding: 6px;
    background: #04070a; border: 1px solid var(--hairline); display: flex; flex-direction: column; gap: 5px; }
  .pf { width: 100%; background: none; border: 1px solid var(--hairline); color: var(--ink);
    font: inherit; font-size: 9px; padding: 4px 6px; letter-spacing: 0.1em; }
  .plist { max-height: 210px; overflow-y: auto; display: flex; flex-direction: column; }
  .pitem { display: flex; align-items: center; gap: 7px; text-align: left; padding: 5px 6px;
    background: none; border: none; color: var(--ink-dim); font-size: 9px; letter-spacing: 0.08em;
    cursor: crosshair; }
  .pitem:hover, .pitem.on { background: rgba(56,208,227,0.1); color: var(--cyan); }
  .pfam { width: 5px; height: 5px; border-radius: 50%; background: var(--ink-ghost); flex: 0 0 auto; }
  .f-presence, .f-spatial { background: var(--ink-dim); }
  .f-appearance { background: var(--ink); } .f-identity { background: var(--cyan); }
  .f-behaviour { background: var(--jade); } .f-system { background: var(--scarlet); }
  .phint { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.1em; }

  .rel { width: 60px; height: 18px; }
  .ra { fill: var(--cyan); opacity: 0.8; } .rb { fill: var(--ink-dim); }

  .wins { display: flex; gap: 4px; flex-wrap: wrap; }
  .win { padding: 4px 8px; border: 1px solid var(--hairline); background: none; color: var(--ink-dim);
    font-size: 8px; letter-spacing: 0.12em; cursor: crosshair; }
  .win.on { border-color: var(--cyan); color: var(--cyan); }

  .ask { position: relative; }
  .askin { width: 100%; background: none; border: 1px solid var(--hairline); color: var(--ink);
    font: inherit; font-size: 10px; padding: 7px 8px; letter-spacing: 0.08em; }
  .askin:focus { outline: none; border-color: var(--cyan); }
  .think { position: absolute; right: 8px; top: 9px; font-size: 8px; color: var(--cyan);
    letter-spacing: 0.14em; animation: pulse 1.1s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.4; } }

  .err { display: flex; flex-direction: column; gap: 6px; padding: 8px; border: 1px solid
    color-mix(in srgb, var(--amber) 45%, transparent); background: var(--amber-dim); }
  .etxt { font-size: 9px; color: var(--amber); letter-spacing: 0.1em; line-height: 1.5; }
  .efix { align-self: flex-start; padding: 4px 8px; border: 1px solid var(--amber); background: none;
    color: var(--amber); font-size: 8px; letter-spacing: 0.12em; cursor: crosshair; }

  .acts { display: flex; gap: 7px; margin-top: 4px; }
  .run { flex: 1; padding: 9px 0; border: 1px solid var(--cyan); background: none; color: var(--cyan);
    font-size: 10px; letter-spacing: 0.16em; cursor: crosshair; }
  .run:hover:not(:disabled) { background: var(--cyan); color: #04070a; }
  .run:disabled { opacity: 0.5; }
  .clr { padding: 9px 12px; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim);
    font-size: 10px; letter-spacing: 0.14em; cursor: crosshair; }
  .clr:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .sugs { display: flex; flex-direction: column; gap: 4px; }
  .sug { display: flex; align-items: center; gap: 8px; text-align: left; padding: 6px 8px;
    border: 1px solid var(--hairline); background: none; color: var(--ink-dim); font-size: 8px;
    letter-spacing: 0.1em; cursor: crosshair; }
  .sug:hover { border-color: var(--cyan); color: var(--cyan); }
  .stxt { flex: 1; line-height: 1.5; } .sn { color: var(--ink-ghost); }
</style>
