<script lang="ts">
  import { api, message } from './api'
  import type { Metric, Report } from './types'

  const variants: Record<string, string> = { info: 'secondary', warning: 'warning', critical: 'danger' }

  let reports = $state<Report[] | null>(null)
  let metrics = $state<Metric[]>([])
  let error = $state('')

  api<Report[]>('GET', '/reports').then((r) => (reports = r), (e) => (error = message(e)))
  api<Metric[]>('GET', '/metrics').then((m) => (metrics = m), () => {}) // only for friendlier labels

  const label = (path: string) => metrics.find((m) => m.path === path)?.label ?? path
  const unit = (path: string) => metrics.find((m) => m.path === path)?.unit ?? ''

  function ago(t: number) {
    const s = Date.now() / 1000 - t
    if (s < 60) return 'just now'
    if (s < 3600) return `${Math.floor(s / 60)} min ago`
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`
    return new Date(t * 1000).toLocaleDateString()
  }
</script>

<div class="page-head">
  <div>
    <h2>History</h2>
    <p class="text-light">The last 50 reports, newest first.</p>
  </div>
</div>

{#if reports}
  <div class="vstack gap-3">
    {#each reports as r (r.id)}
      <details class="card">
        <summary class="hstack gap-3">
          <span class="badge" data-variant={variants[r.report.severity] ?? 'secondary'}>{r.report.severity}</span>
          <strong class="title">{r.report.title}</strong>
          <time class="text-light small" title={new Date(r.time * 1000).toLocaleString()}>{ago(r.time)}</time>
        </summary>
        <div class="mt-4">
          <p>{r.report.summary}</p>
          {#if r.report.findings.length}
            <h4>Findings</h4>
            <ul>{#each r.report.findings as f}<li>{f}</li>{/each}</ul>
          {/if}
          {#if r.report.actions.length}
            <h4>Suggested actions</h4>
            <ul>{#each r.report.actions as a}<li>{a}</li>{/each}</ul>
          {/if}
          <footer class="text-light small">
            {#each r.alerts as a}
              <div>Triggered by {label(a.path)} at <strong>{a.measured} {unit(a.path)}</strong> ({a.op} {a.value})</div>
            {/each}
          </footer>
        </div>
      </details>
    {:else}
      <article class="card empty">
        <p><strong>Nothing has fired yet.</strong></p>
        <p class="text-light">Reports show up here when an alert fires, even with no sinks enabled.</p>
      </article>
    {/each}
  </div>
{:else if error}
  <div role="alert" data-variant="error">{error}</div>
{:else}
  <div class="skeleton" style="height: 8rem"></div>
{/if}

<style>
  summary { cursor: pointer; list-style: none; }
  summary::-webkit-details-marker { display: none; }
  .title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  time { white-space: nowrap; }
  h4 { margin: var(--space-4) 0 var(--space-1); font-size: var(--text-6); }
  footer { border-top: 1px solid var(--border); margin-top: var(--space-4); padding-top: var(--space-3); }
  .empty { text-align: center; padding-block: var(--space-10); }
  .empty p { margin: 0; }
</style>
