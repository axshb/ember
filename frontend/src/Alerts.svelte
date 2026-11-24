<script lang="ts">
  import { api, message } from './api'
  import type { AlertRule, Config, Metric } from './types'

  const ops: [string, string][] = [['>', 'above'], ['<', 'below'], ['>=', 'at or above'], ['<=', 'at or below']]
  const cooldowns: [number, string][] = [[0, 'no cooldown'], [5, '5 min cooldown'], [15, '15 min cooldown'], [60, '1 h cooldown'], [360, '6 h cooldown'], [1440, '1 day cooldown']]
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
    config!.alerts.push({ path: m?.path ?? '', op: '>', value: m?.unit === '%' ? 80 : 0, cooldown: 15 })
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
    <p class="text-light">Checked every poll. When one fires, the agent investigates and sends a report.</p>
  </div>
  {#if config}<button class="outline" onclick={add} disabled={!metrics.length}>+ Add alert</button>{/if}
</div>

{#if config}
  {#if !metrics.length}
    <div role="alert" data-variant="warning" class="mb-4">No metrics yet, ember hasn't finished its first poll. Reload in a few seconds.</div>
  {/if}

  <div class="vstack gap-4">
    {#each config.alerts as rule, i}
      {@const m = find(rule.path)}
      {@const firing = m && compare[rule.op]?.(m.value, rule.value)}
      <article class="card rule">
        <div class="sentence">
          <select bind:value={rule.path} aria-label="Metric" class="metric">
            {#if !m}<option value={rule.path}>{rule.path || 'Choose a metric'}</option>{/if}
            {#each groups as [group, items]}
              <optgroup label={cap(group)}>
                {#each items! as item}<option value={item.path}>{cap(item.group)} › {item.name} ({item.unit})</option>{/each}
              </optgroup>
            {/each}
          </select>
          <span class="hstack gap-3">
            <span class="text-light">is</span>
            <select bind:value={rule.op} aria-label="Condition" class="op">
              {#each ops as [op, label]}<option value={op}>{label}</option>{/each}
            </select>
          </span>
          <fieldset class="group threshold">
            <input id="threshold-{i}" type="number" step="any" bind:value={rule.value} aria-label="Threshold" />
            {#if m}<label for="threshold-{i}">{m.unit}</label>{/if}
          </fieldset>
          <select bind:value={rule.cooldown} aria-label="Cooldown" class="cooldown">
            {#if !cooldowns.some(([v]) => v === rule.cooldown)}<option value={rule.cooldown}>{rule.cooldown} min cooldown</option>{/if}
            {#each cooldowns as [v, label]}<option value={v}>{label}</option>{/each}
          </select>
          <button class="ghost small remove" data-variant="danger" title="Remove alert" aria-label="Remove alert" onclick={() => config!.alerts.splice(i, 1)}>✕</button>
        </div>

        {#if m}
          {@const bar = meter(rule, m)}
          <div class="hstack gap-4 mt-4">
            <meter value={m.value} min="0" {...bar}></meter>
            <span class="now">now <strong>{fmt(m.value, m.unit)}</strong></span>
            <span class="badge" data-variant={firing ? 'danger' : 'success'}>{firing ? 'firing' : 'ok'}</span>
          </div>
        {:else}
          <p class="text-light small mt-4" style="margin-bottom: 0">Not reporting right now (stopped container or removed disk?). The rule is kept.</p>
        {/if}
      </article>
    {:else}
      <article class="card empty">
        <p><strong>No alerts yet.</strong></p>
        <p class="text-light">Add one to get a report when a metric crosses a threshold, like disk above 90%.</p>
      </article>
    {/each}
  </div>

  {#if dirty || justSaved || error}
    <div class="savebar card hstack justify-between">
      {#if error}<span role="alert" data-variant="error">{error}</span>
      {:else if justSaved}<span>Saved.</span>
      {:else}<span class="text-light">Unsaved changes</span>{/if}
      <button onclick={save} disabled={!dirty}>Save</button>
    </div>
  {/if}
{:else if error}
  <div role="alert" data-variant="error">{error}</div>
{:else}
  <div class="skeleton" style="height: 8rem"></div>
{/if}

<style>
  .sentence { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); }
  .sentence > * { margin: 0; }
  .metric { flex: 1 1 100%; }
  .op, .cooldown { width: auto; }
  .threshold { width: 8rem; }
  .remove { margin-left: auto; }
  @media (min-width: 769px) { .metric { flex-basis: 14rem; } }
  meter { flex: 1; }
  .now { white-space: nowrap; font-variant-numeric: tabular-nums; }
  .empty { text-align: center; padding-block: var(--space-10); }
  .empty p { margin: 0; }
  .savebar { position: sticky; bottom: var(--space-4); margin-top: var(--space-6); padding: var(--space-3) var(--space-4); box-shadow: var(--shadow-large); }
</style>
