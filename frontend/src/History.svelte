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
    <p class="text-light small">The last 50 reports, newest first.</p>
  </div>
</div>

{#if reports}
  <div class="card list">
    {#each reports as r (r.id)}
      <details>
        <summary>
          <span class="badge" data-variant={variants[r.report.severity] ?? 'secondary'}>{r.report.severity}</span>
          <strong class="title">{r.report.title}</strong>
          <time class="text-light" title={new Date(r.time * 1000).toLocaleString()}>{ago(r.time)}</time>
        </summary>
        <div class="body">
          <p>{r.report.summary}</p>
          {#if r.report.findings.length}
            <h4>Findings</h4>
            <ul>{#each r.report.findings as f}<li>{f}</li>{/each}</ul>
          {/if}
          {#if r.report.actions.length}
            <h4>Suggested actions</h4>
            <ul>{#each r.report.actions as a}<li>{a}</li>{/each}</ul>
          {/if}
          <footer class="text-light">
            {#each r.alerts as a}
              {#if a.source === 'docker'}
                <div>{a.state === 'resolved' ? 'Cleared' : 'Triggered'}: container <strong>{a.name}</strong> {a.problem}{a.problem === 'crashed' ? ` (exit ${a.exit_code})` : ''}</div>
              {:else}
                <div>{a.state === 'resolved' ? 'Cleared' : 'Triggered'}: {label(a.path)} at <strong>{a.measured} {unit(a.path)}</strong> ({a.op} {a.value})</div>
              {/if}
            {/each}
          </footer>
        </div>
      </details>
    {:else}
      <p class="empty text-light">Nothing has fired yet. Reports show up here even with no sinks enabled.</p>
    {/each}
  </div>
{:else if error}
  <div role="alert" data-variant="error">{error}</div>
{:else}
  <div class="skeleton" style="height: 8rem"></div>
{/if}

<style>
  .list { padding: 0; }
  /* oat boxes every details, here they're plain rows inside one card */
  details { border: 0; border-radius: 0; border-bottom: 1px solid var(--border); margin: 0; }
  details:last-child { border-bottom: 0; }
  summary { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-2) var(--space-3); font-size: var(--text-7); }
  .title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  time { white-space: nowrap; font-size: var(--text-8); }
  .body { margin: 0; padding: var(--space-2) var(--space-3) var(--space-3); font-size: var(--text-7); }
  .body p { margin: 0 0 var(--space-2); }
  h4 { margin: var(--space-3) 0 var(--space-1); font-size: var(--text-7); }
  ul { margin: 0; }
  footer { border-top: 1px solid var(--border); margin-top: var(--space-3); padding-top: var(--space-2); font-size: var(--text-8); }
  .empty { margin: 0; padding: var(--space-6); text-align: center; }
</style>
