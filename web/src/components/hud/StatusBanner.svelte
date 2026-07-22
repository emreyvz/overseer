<script lang="ts">
  // Center status banner: red solid = alarm, black/white = neutral (brief §7.c).
  import { banner } from '../../lib/stores'
</script>

{#if $banner}
  <div class="banner caps" class:alarm={$banner.alarm}>
    <span>{$banner.text}</span>
  </div>
{/if}

<style>
  .banner {
    position: absolute; left: 50%; bottom: 16vh; transform: translateX(-50%);
    z-index: var(--z-banner); padding: 7px 20px;
    background: #000; border: 1px solid var(--ink); color: var(--ink);
    font-size: var(--fs-banner); letter-spacing: var(--tracking);
    animation: pop 120ms var(--ease);
  }
  .banner.alarm {
    background: var(--scarlet); border-color: var(--scarlet); color: #fff;
    box-shadow: 0 0 18px var(--scarlet-glow); animation: pulse 0.9s ease 2;
  }
  @keyframes pop { from { opacity: 0; transform: translateX(-50%) scale(0.96); } }
  @keyframes pulse { 50% { box-shadow: 0 0 30px var(--scarlet-hot); } }
</style>
