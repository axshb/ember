<script lang="ts">
  import { api, message } from './api'
  import type { AlertRule, Config, Metric } from './types'

  const ops: [string, string][] = [['>', 'above'], ['<', 'below'], ['>=', 'at or above'], ['<=', 'at or below']]
  const reminders: [number, string][] = [[0, 'none'], [60, 'hourly'], [360, 'every 6 h'], [1440, 'daily']]
  const compare: Record<string, (a: number, b: number) => boolean> = {
    '>': (a, b) => a > b, '<': (a, b) => a < b, '>=': (a, b) => a >= b, '<=': (a, b) => a <= b,
  }

  let config = $state<Config | null>(null)
  let metrics = $state<Metric[]>([])
  let savedJson = $state('')
  let error = $state('')
  let justSaved = $state(false)

  api<Config>('GET', '/config').then((c) => { config = c; savedJson = JSON.stringify(c.alerts) }, (e) => (error = message(e)))
  api<Metric[]>('GET', '/metrics').then((m) => (metrics = m), (e) => (error = message(e)))

  const dirty = $derived(config !== null && JSON.stringify(config.alerts) !== savedJson)
  const groups = $derived(Object.entries(Object.groupBy(metrics, (m) => m.group)))
  const find = (path: string) => metrics.find((m) => m.path === path)
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
  const fmt = (v: number, unit: string) => `${Math.round(v * 10) / 10}${unit === '%' ? '%' : ` ${unit}`}`

  // meter: green well inside the threshold, amber near it, red past it
  function meter(rule: AlertRule, m: Metric) {
    const max = m.unit === '%' ? 100 : Math.max(m.value, rule.value) * 1.25 || 1
    const above = rule.op.startsWith('>')
    return above
      ? { max, low: rule.value * 0.8, high: rule.value, optimum: 0 }
      : { max, low: rule.value, high: rule.value * 1.2, optimum: max }
  }

  function add() {
    const m = metrics[0]
    config!.alerts.push({ path: m?.path ?? '', op: '>', value: m?.unit === '%' ? 80 : 0, remind: 0 })
  }

  async function save() {
    error = ''
    try {
      await api('PUT', '/config', config)
      savedJson = JSON.stringify(config!.alerts)
      justSaved = true
      setTimeout(() => (justSaved = false), 2000)
    } catch (e) {
      error = message(e)
    }
  }
</script>

<div class="page-head">
  <div>
    <h2>Alerts</h2>
    <p class="text-light small">Checked every poll. A firing rule gets one report from the agent, then a short message when it clears. Container crashes, restart loops and failing healthchecks are always watched.</p>
  </div>
  {#if config}<button class="outline small" onclick={add} disabled={!metrics.length}>+ Add</button>{/if}
</div>

{#if config}
  {#if !metrics.length}
    <div role="alert" data-variant="warning" class="mb-4">No metrics yet, ember hasn't finished its first poll. Reload in a few seconds.</div>
  {/if}

  <div class="rules card">
    {#if config.alerts.length}
      <div class="row head text-light">
        <span>Metric</span><span>Condition</span><span>Threshold</span><span>Reminders</span><span>Now</span><span></span><span></span>
      </div>
    {/if}
    {#each config.alerts as rule, i}
      {@const m = find(rule.path)}
      {@const firing = m && compare[rule.op]?.(m.value, rule.value)}
      <div class="row">
        <select bind:value={rule.path} aria-label="Metric" class="metric">
          {#if !m}<option value={rule.path}>{rule.path || 'Choose a metric'}</option>{/if}
          {#each groups as [group, items]}
            <optgroup label={cap(group)}>
              {#each items! as item}<option value={item.path}>{cap(item.group)} › {item.name} ({item.unit})</option>{/each}
            </optgroup>
          {/each}
        </select>
        <select bind:value={rule.op} aria-label="Condition">
          {#each ops as [op, label]}<option value={op}>{label}</option>{/each}
        </select>
        <fieldset class="group">
          <input id="threshold-{i}" type="number" step="any" bind:value={rule.value} aria-label="Threshold" />
          {#if m}<label for="threshold-{i}">{m.unit}</label>{/if}
        </fieldset>
        <select bind:value={rule.remind} aria-label="Reminders while firing">
          {#if !reminders.some(([v]) => v === rule.remind)}<option value={rule.remind}>every {rule.remind} min</option>{/if}
          {#each reminders as [v, label]}<option value={v}>{label}</option>{/each}
        </select>
        {#if m}
          <span class="now"><meter value={m.value} min="0" {...meter(rule, m)}></meter>{fmt(m.value, m.unit)}</span>
          <span class="badge" data-variant={firing ? 'danger' : 'success'}>{firing ? 'firing' : 'ok'}</span>
        {:else}
          <span class="now text-light" title="Stopped container or removed disk? The rule is kept.">not reporting</span>
          <span></span>
        {/if}
        <button class="ghost small" data-variant="danger" title="Remove alert" aria-label="Remove alert" onclick={() => config!.alerts.splice(i, 1)}>✕</button>
      </div>
    {:else}
      <p class="empty text-light">No alerts yet. Add one to get a report when a metric crosses a threshold, like disk above 90%.</p>
    {/each}
  </div>

  {#if dirty || justSaved || error}
    <div class="savebar card hstack justify-between">
      {#if error}<span role="alert" data-variant="error">{error}</span>
      {:else if justSaved}<span>Saved.</span>
      {:else}<span class="text-light">Unsaved changes</span>{/if}
      <button class="small" onclick={save} disabled={!dirty}>Save</button>
    </div>
  {/if}
{:else if error}
  <div role="alert" data-variant="error">{error}</div>
{:else}
  <div class="skeleton" style="height: 8rem"></div>
{/if}

<style>
  .rules { padding: 0; }
  .row {
    display: grid; align-items: center; gap: var(--space-2);
    grid-template-columns: minmax(10rem, 2fr) 7rem 6.5rem 7.5rem minmax(7rem, 1fr) 3.5rem 2rem;
    padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--border);
  }
  .row:last-child { border-bottom: 0; }
  .row > * { margin: 0; min-width: 0; }
  .head { font-size: var(--text-8); padding-block: var(--space-2) 0; border-bottom: 0; }
  .row select, .row input { height: 2rem; padding-block: 0; font-size: var(--text-7); }
  .row fieldset label { font-size: var(--text-8); }
  .now { display: flex; align-items: center; gap: var(--space-2); font-size: var(--text-7); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .now meter { width: 3.5rem; flex: none; }
  .badge { justify-self: start; }
  .empty { margin: 0; padding: var(--space-6); text-align: center; }
  .savebar { position: sticky; bottom: var(--space-4); margin-top: var(--space-4); padding: var(--space-2) var(--space-3); box-shadow: var(--shadow-large); }
  /* phone: metric on its own line, the rest wraps under it */
  @media (max-width: 900px) {
    .head { display: none; }
    .row { grid-template-columns: 1fr 1fr 1fr; }
    .row > button { justify-self: end; }
    .metric { grid-column: 1 / -1; }
  }
</style>
