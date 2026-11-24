<h1 align="center">ember</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.14-blue" alt="Python 3.14">
  <img src="https://img.shields.io/badge/Docker-compose-2496ED" alt="Docker Compose">
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs Welcome">
  <img src="https://img.shields.io/github/last-commit/axshb/ember" alt="Last Commit">
</p>

ember is a small alert reporter for a single server. It watches host and container metrics, and when an alert fires, an LLM agent investigates it with sandboxed, read-only tools and writes up what it found. Reports go to Discord or email. Nothing listens for replies, so every sink is one-way.

Everything is set up from a web UI: alert rules, the agent, sinks, and the prompts the agent follows. Recent reports show up there too.

## Installation

You'll need Docker with Compose.

```
git clone https://github.com/axshb/ember.git
cd ember
docker compose up -d --build
```

Then open http://localhost:7340.

On a remote server, use an SSH tunnel (`ssh -L 7340:localhost:7340 yourserver`) or put something with auth in front of it, like Tailscale or Caddy. If you reach it through another hostname, add it to `EMBER_ALLOWED_HOSTS` in `docker-compose.yml`.

## Usage

1. **Settings:** add an [OpenRouter](https://openrouter.ai) API key and pick a model. Without a key, reports contain the raw alert data only.
2. **Settings:** turn on a sink. For Discord, create a webhook in your channel (Settings → Integrations → Webhooks). For email, fill in your SMTP details.
3. **Alerts:** add a rule, e.g. *disk /dev/sda1 usage is above 90%*. Each rule shows the live value, and whether it's currently firing.
4. **History:** reports land here, whether or not a sink is enabled.

Config, prompt overrides and the last 50 reports live in a sqlite file in the `ember-data` volume. It survives rebuilds; only `docker compose down -v` deletes it. Settings has export/import for backups, and exports never contain secrets.

## Security

ember has no login of its own. The UI is published on `127.0.0.1` only. Keep it that way unless something with auth sits in front, since Docker-published ports bypass ufw.

The container runs read-only with all capabilities dropped except `SETUID`, `SETGID` and `KILL`, `no-new-privileges`, and pid/memory limits. The agent's shell commands run as UID `10001` with a clean environment and can't reach the Docker socket, the database, or the web UI. They can still read anything world-readable on the host through `/host`, see host process command lines, and make network connections, so don't keep secrets world-readable or in command-line arguments.

## Development

```
cd frontend && bun install && bun run build && cd ..
uv run python main.py
```

The UI is on http://localhost:8080 and data goes to `./data`. For frontend work, `bun run dev` in `frontend/` proxies the API to the running backend.

```
uv run pytest
cd frontend && bun run check
```

Tests mock external calls, so no API keys, Docker daemon or notification services are needed.

## Contributing

Contributions are welcome. Fork the repository and submit a pull request.
