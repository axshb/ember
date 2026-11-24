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

<div class="page-head">
  <div>
    <h2>Prompts</h2>
    <p class="text-light">Instructions the agent follows when writing a report. Edits apply to the next alert.</p>
  </div>
</div>

{#if prompts}
  <div class="vstack gap-4">
    {#each prompts as p}
      <article class="card">
        <header class="hstack justify-between mb-4">
          <code>{p.name}</code>
          {#if p.override === null}
            <span class="badge" data-variant="secondary">default</span>
          {:else}
            <span class="badge" data-variant="warning">customized</span>
          {/if}
        </header>
        <textarea rows="22" bind:value={p.text} aria-label={p.name}></textarea>
        <footer class="hstack gap-3 mt-4">
          <button onclick={() => save(p)} disabled={p.text === (p.override ?? p.default)}>Save</button>
          <button class="ghost" disabled={p.override === null} onclick={() => reset(p)}>Reset to default</button>
          {#if p.failed}<span role="alert" data-variant="error">{p.msg}</span>{:else if p.msg}<span class="text-light small">{p.msg}</span>{/if}
        </footer>
      </article>
    {:else}
      <p class="text-light">No prompts.</p>
    {/each}
  </div>
{:else if error}
  <div role="alert" data-variant="error">{error}</div>
{:else}
  <div class="skeleton" style="height: 24rem"></div>
{/if}
