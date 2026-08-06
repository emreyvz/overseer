<script lang="ts">
  // The header block that answers "what is this and what do I do here", on every perception
  // screen, before anything else.
  //
  // Every one of these screens opened straight into instrumentation. You got a queue, three
  // panels, a chart and a dozen abbreviations, with nothing anywhere saying what the screen was
  // FOR. The information was all present and the point of it was missing. The operator's words:
  // "I opened it somehow, but I still do not know where to look or what to do."
  //
  // So it is three things, in a fixed order, in decreasing size: what this IS, what you DO here,
  // and the note that underlined terms explain themselves. Deliberately not ALL CAPS and not
  // letter-spaced — it should read as a person talking, which makes it visibly different from
  // the machine labels around it rather than more chrome to decode.
  let {
    what,
    hint,
    /** The single most useful thing to look at first, when a screen has an obvious answer. */
    look,
  }: { what: string; hint?: string; look?: string } = $props()
</script>

<div class="intro">
  <div class="txt">
    <p class="w">{what}</p>
    {#if hint}<p class="h"><span class="arw">→</span>{hint}</p>{/if}
  </div>
  {#if look}<p class="look"><span class="lk">START HERE</span>{look}</p>{/if}
  <p class="k">Anything <span class="mk">underlined<span class="q">?</span></span> explains itself</p>
</div>

<style>
  .intro { display: flex; align-items: center; gap: 26px; flex-wrap: wrap;
    padding: 13px 22px; border-bottom: 1px solid var(--hairline);
    background: linear-gradient(90deg, rgba(56,208,227,0.07), transparent 62%);
    border-left: 2px solid var(--cyan); }
  .txt { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  .w { font-size: 15px; color: var(--ink); line-height: 1.45; margin: 0; }
  .h { display: flex; gap: 7px; font-size: 12px; color: var(--ink-dim); line-height: 1.5; margin: 0; }
  .arw { color: var(--cyan); flex: 0 0 auto; }
  .look { display: flex; align-items: center; gap: 9px; font-size: 12px; color: var(--ink-dim);
    line-height: 1.5; margin: 0; max-width: 340px; }
  .lk { flex: 0 0 auto; font-size: 8px; color: var(--cyan); letter-spacing: 0.18em;
    border: 1px solid color-mix(in srgb, var(--cyan) 45%, transparent); padding: 3px 6px; }
  .k { margin: 0 0 0 auto; font-size: 11px; color: var(--ink-ghost); }
  .mk { border-bottom: 1px dotted color-mix(in srgb, var(--cyan) 60%, transparent); color: var(--cyan); }
  .q { font-size: 0.72em; vertical-align: super; margin-left: 2px; }

  @media (max-width: 1100px) {
    .k { display: none; }
  }
</style>
