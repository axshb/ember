# ember

A read-only alert reporter for a single server. Ember polls host and Docker
metrics, evaluates threshold alerts, has an LLM agent triage what fired (using
read-only tools), and sends a structured report to your sinks. Each alert fires
once and sends a short notice when it resolves. Nothing listens for replies —
every sink is one-way.

## Sinks

- **discord** — webhook. Create one in your notification channel
  (Settings → Integrations → Webhooks) and set `[discord] webhook_url`.
  Reports arrive as embeds, color-coded by severity.
- **email** — plain SMTP, STARTTLS by default (`[email]` section).
- slack / telegram — not implemented; toggles are reserved in `[sinks]`.

## Configuration

Copy `config.example.toml` to `config.toml` in the repo root and fill it in.
Secrets live only in `config.toml`, which is gitignored.

### Prompts

The agent's instructions are every `*.md` file in `prompts/` plus
`prompts/defaults/`, concatenated in sorted filename order. A file in
`prompts/` (gitignored) replaces the same-named default wholesale, so
`git pull` updates the defaults without touching your customizations.

`prompts/defaults/triage.md` is the out-of-the-box triage prompt. Override it
with your own `prompts/triage.md`, or add new `*.md` files next to it.

## Tests

    uv run pytest

Tests mock external calls; no API keys, Docker daemon, or notification services required.

## Run

    docker compose up -d --build

or locally:

    uv run python main.py

The container mounts the Docker socket (metrics), `/` at `/host` read-only,
and `config.toml` / `prompts/` read-only from the host.

### Commands and checks

`[[commands]]` in `config.toml` adds your own commands (see the example config).
They run on the host as uid 2100000000, so they can't use the Docker socket,
write to the host, or read root-only files. Make what they need readable to
that uid. The agent can call them as tools, and with `every` ember also runs
them on a schedule and alerts when one exits non-zero. Checks run when ember
starts and then on their schedule, so keep them to checks rather than jobs.
A slow check delays that poll's other alerts, up to the 2 minute timeout.
Commands need the container, since running them needs root to switch users.

### Agent tools

The agent has no shell. It can only call a fixed set of read-only tools (the
busiest processes, folder sizes, the kernel log, and container state and logs)
plus your own commands, exactly as written in the config. The built-in tools
can't change anything on the host. Your commands can reach the network, so
they can do whatever you wrote them to do.

What the tools return is sent to your LLM provider, including container logs
and the output of your commands.
To keep it on the machine, point `base_url` at a local model. To skip the LLM
entirely, leave out the API key and ember sends the raw alerts.
