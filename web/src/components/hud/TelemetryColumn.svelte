<script lang="ts">
  // Left-edge "system alive" decor telemetry (brief §4.4). Ghost text.
  const ROWS = [
    '+0.0/VCM', '+0.1/VCM', '', 'INPUT_1', 'INPUT_2', 'INPUT_3', 'INPUT_4',
    'INPUT_5', 'INPUT_6', 'INPUT_7', 'INPUT_8', 'INPUT_FEED', '',
    'DO1 CTRL', 'DO2 CTRL', 'ADMIN_CTRL', '[LOCKED]', '', 'CTL', 'SYS', 'SNS',
  ]
</script>
<div class="tel caps" aria-hidden="true">
  {#each ROWS as r}<div class="row">{r}</div>{/each}
</div>
<style>
  .tel { position: absolute; left: 26px; top: 20%; z-index: var(--z-overlay);
    font-size: var(--fs-micro); letter-spacing: 0.1em; color: var(--ink-ghost); line-height: 1.65; pointer-events: none; }
  .row { height: 12px; }
</style>
