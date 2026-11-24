<script lang="ts">
  import { api, message } from './api'
  import type { Config } from './types'
  import Prompts from './Prompts.svelte'

  let config = $state<Config | null>(null)
  let isSet = $state({ api_key: false, webhook_url: false, password: false }) // as of last load
  let error = $state('')
  let saved = $state(false)
  let importMsg = $state('')
  let imports = $state(0) // an import replaces the prompts too, this remounts them

  async function load() {
    try {
      const c = await api<Config>('GET', '/config')
      config = c
      isSet = {
        api_key: c.agent.api_key !== '',
        webhook_url: c.discord.webhook_url !== '',
        password: c.email.password !== '',
      }
    } catch (e) {
      error = message(e)
    }
  }
  load()

  async function save(e: SubmitEvent) {
    e.preventDefault()
    error = ''
    saved = false
    try {
      await api('PUT', '/config', config)
      saved = true
      await load()
    } catch (e) {
      error = message(e)
    }
  }

  async function importFile(e: Event) {
    const input = e.currentTarget as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file || !confirm(`Import ${file.name}? This replaces the current config and prompts.`)) return
    importMsg = ''
    try {
      await api('POST', '/import', await file.text())
      await load()
      imports++
      importMsg = `Imported ${file.name}.`
    } catch (err) {
      importMsg = `Import failed: ${message(err)}`
    }
  }

  // select the mask on focus so typing replaces it instead of appending
  const selectAll = (e: FocusEvent) => (e.currentTarget as HTMLInputElement).select()
</script>

<div class="settings">
<div class="page-head">
  <div>
    <h2>Settings</h2>
    <p class="text-light small">Changes apply on the next poll, no restart needed.</p>
  </div>
</div>

{#snippet secret(set: boolean)}
  <span class="badge" data-variant={set ? 'success' : 'secondary'}>{set ? 'set' : 'not set'}</span>
{/snippet}

{#if config}
  <form onsubmit={save} class="vstack gap-3">
    <section class="card">
      <h3>Agent</h3>
      <p class="text-light small">The LLM that investigates alerts, via OpenRouter. Without a key, reports contain the raw alert data only.</p>
      <label data-field>
        <span class="hstack gap-2">OpenRouter API key {@render secret(isSet.api_key)}</span>
        <input type="password" autocomplete="new-password" placeholder="sk-or-..." bind:value={config.agent.api_key} onfocus={selectAll} />
      </label>
      <div class="row">
        <label data-field class="col-8">Model <input bind:value={config.agent.model} /></label>
        <label data-field class="col-4">Max tool calls <input type="number" min="1" bind:value={config.agent.max_tool_calls} /></label>
      </div>
      <details>
        <summary class="small">Advanced</summary>
        <div class="row mt-2">
          <label data-field class="col-6">Shell timeout (s) <input type="number" min="1" step="any" bind:value={config.agent.shell.default_timeout} /></label>
          <label data-field class="col-6">Max shell output (chars) <input type="number" min="1" bind:value={config.agent.shell.max_output_chars} /></label>
        </div>
        <label data-field>Check every (seconds) <input type="number" min="1" bind:value={config.polling.interval} /></label>
      </details>
    </section>

    <section class="card">
      <header class="hstack justify-between">
        <h3>Discord</h3>
        <input type="checkbox" role="switch" aria-label="Send reports to Discord" bind:checked={config.sinks.discord} />
      </header>
      {#if config.sinks.discord}
        <label data-field class="mt-4">
          <span class="hstack gap-2">Webhook URL {@render secret(isSet.webhook_url)}</span>
          <input type="password" autocomplete="new-password" placeholder="https://discord.com/api/webhooks/..." bind:value={config.discord.webhook_url} onfocus={selectAll} />
        </label>
      {:else}
        <p class="text-light small">Off. Channel settings → Integrations → Webhooks to create one.</p>
      {/if}
    </section>

    <section class="card">
      <header class="hstack justify-between">
        <h3>Email</h3>
        <input type="checkbox" role="switch" aria-label="Send reports by email" bind:checked={config.sinks.email} />
      </header>
      {#if config.sinks.email}
        <div class="row mt-4">
          <label data-field class="col-9">SMTP host <input bind:value={config.email.host} /></label>
          <label data-field class="col-3">Port <input type="number" bind:value={config.email.port} /></label>
        </div>
        <div class="row">
          <label data-field class="col-6">Username <input autocomplete="off" bind:value={config.email.username} /></label>
          <label data-field class="col-6">
            <span class="hstack gap-2">Password {@render secret(isSet.password)}</span>
            <input type="password" autocomplete="new-password" bind:value={config.email.password} onfocus={selectAll} />
          </label>
        </div>
        <div class="row">
          <label data-field class="col-6">From <input bind:value={config.email.from} /></label>
          <label data-field class="col-6">To <input bind:value={config.email.to} /></label>
        </div>
        <label class="hstack gap-2"><input type="checkbox" role="switch" bind:checked={config.email.starttls} /> STARTTLS</label>
      {:else}
        <p class="text-light small">Off.</p>
      {/if}
    </section>

    <div class="hstack gap-3">
      <button class="small">Save settings</button>
      {#if saved}<span class="text-light">Saved.</span>{/if}
      {#if error}<span role="alert" data-variant="error">{error}</span>{/if}
    </div>
  </form>

  {#key imports}<Prompts />{/key}

  <section class="card">
    <h3>Backup</h3>
    <p class="text-light small">Alerts, settings and prompts as a file. Secrets (API key, webhook URL, email password) are never included.</p>
    <div class="hstack gap-3">
      <a class="button outline small" href="/api/export" download>Export</a>
      <label class="button outline small">Import… <input type="file" accept=".json,application/json" onchange={importFile} hidden /></label>
      {#if importMsg}<span class="small">{importMsg}</span>{/if}
    </div>
  </section>
{:else if error}
  <div role="alert" data-variant="error">{error}</div>
{:else}
  <div class="skeleton" style="height: 16rem"></div>
{/if}
</div>

<style>
  /* global so the Prompts section inside this page matches */
  .settings :global(section.card) { padding: var(--space-4); }
  .settings > :global(section.card) { margin-top: var(--space-3); }
  .settings :global(section.card h3) { margin: 0; font-size: var(--text-5); }
  .settings :global(section.card > p.small) { margin: var(--space-1) 0 var(--space-3); }
  .settings section.card > header + p.small { margin-bottom: 0; }
</style>
