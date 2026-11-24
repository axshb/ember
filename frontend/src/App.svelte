<script lang="ts">
  import Alerts from './Alerts.svelte'
  import Settings from './Settings.svelte'
  import History from './History.svelte'

  const views: Record<string, [string, typeof Alerts]> = {
    alerts: ['Alerts', Alerts],
    history: ['History', History],
    settings: ['Settings', Settings],
  }
  const current = () => (location.hash.slice(1) in views ? location.hash.slice(1) : 'alerts')
  let view = $state(current())
  const View = $derived(views[view][1])
</script>

<svelte:window onhashchange={() => (view = current())} />

<header data-topnav>
  <div class="container hstack justify-between">
    <strong class="brand">ember</strong>
    <nav>
      <div role="tablist">
        {#each Object.entries(views) as [key, [label]]}
          <a role="tab" href="#{key}" aria-selected={view === key}>{label}</a>
        {/each}
      </div>
    </nav>
  </div>
</header>

<main class="container">
  <View />
</main>

<style>
  header > div, main { width: 100%; max-width: 1100px; }
  .brand { font-size: var(--text-6); letter-spacing: -0.02em; }
  .brand::before { content: ''; display: inline-block; width: 0.55em; height: 0.55em; margin-right: 0.45em; border-radius: 50%; background: var(--warning); box-shadow: 0 0 0.6em var(--warning); }
  main { padding-block: var(--space-5) var(--space-10); }
  :global(html) { scrollbar-gutter: stable; } /* no layout jump when a page gets a scrollbar */
  :global(textarea) { font-family: var(--font-mono); font-size: var(--text-7); }
  :global(.page-head) { display: flex; align-items: end; justify-content: space-between; gap: var(--space-4); margin-bottom: var(--space-3); }
  :global(.page-head h2) { margin: 0; font-size: var(--text-4); }
  :global(.page-head p) { margin: var(--space-1) 0 0; max-width: 70ch; }
</style>
