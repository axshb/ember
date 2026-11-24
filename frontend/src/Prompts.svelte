<script lang="ts">
  import { api, message } from './api'
  import type { Prompt } from './types'

  type Editable = Prompt & { text: string; msg: string; failed: boolean }

  let prompts = $state<Editable[] | null>(null)
  let error = $state('')

  api<Prompt[]>('GET', '/prompts').then(
    (list) => (prompts = list.map((p) => ({ ...p, text: p.override ?? p.default, msg: '', failed: false }))),
    (e) => (error = message(e)),
  )

  async function save(p: Editable) {
    p.msg = ''
    p.failed = false
    try {
      await api('PUT', `/prompts/${encodeURIComponent(p.name)}`, { body: p.text })
      p.override = p.text
      p.msg = 'Saved.'
    } catch (e) {
      p.msg = message(e)
      p.failed = true
    }
  }

  async function reset(p: Editable) {
    if (!confirm(`Reset ${p.name} to the default? Your changes will be lost.`)) return
    p.msg = ''
    p.failed = false
    try {
      await api('DELETE', `/prompts/${encodeURIComponent(p.name)}`)
      p.override = null
      p.text = p.default
      p.msg = 'Reset to default.'
    } catch (e) {
      p.msg = message(e)
      p.failed = true
    }
  }
</script>

<section class="card">
  <h3>Prompts</h3>
  <p class="text-light small">Instructions the agent follows when writing a report. Edits apply to the next alert.</p>
{#if prompts}
  {#each prompts as p}
    <details>
      <summary class="hstack gap-2">
        <code>{p.name}</code>
        <span class="badge" data-variant={p.override === null ? 'secondary' : 'warning'}>{p.override === null ? 'default' : 'customized'}</span>
      </summary>
      <textarea rows="18" bind:value={p.text} aria-label={p.name}></textarea>
      <div class="hstack gap-3 mt-2">
        <button class="small" onclick={() => save(p)} disabled={p.text === (p.override ?? p.default)}>Save</button>
        <button class="ghost small" disabled={p.override === null} onclick={() => reset(p)}>Reset to default</button>
        {#if p.failed}<span role="alert" data-variant="error">{p.msg}</span>{:else if p.msg}<span class="text-light small">{p.msg}</span>{/if}
      </div>
    </details>
  {:else}
    <p class="text-light small">No prompts.</p>
  {/each}
{:else if error}
  <div role="alert" data-variant="error">{error}</div>
{:else}
  <div class="skeleton" style="height: 4rem"></div>
{/if}
</section>

<style>
  details { margin-top: var(--space-2); }
  summary { justify-content: flex-start; }
  summary::after { margin-left: auto; }
  textarea { margin-top: var(--space-2); }
</style>
