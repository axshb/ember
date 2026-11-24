// shapes returned by the backend, see api/app.py
export interface AlertRule {
  path: string
  op: string
  value: number
  cooldown: number
}

export interface Config {
  polling: { interval: number }
  alerts: AlertRule[]
  agent: {
    api_key: string
    model: string
    max_tool_calls: number
    shell: { default_timeout: number; max_output_chars: number }
  }
  sinks: { discord: boolean; email: boolean }
  discord: { webhook_url: string }
  email: {
    host: string
    port: number
    username: string
    password: string
    from: string
    to: string
    starttls: boolean
  }
}

export interface Metric {
  path: string
  label: string // group + name, e.g. "disk /dev/sda1 usage"
  group: string
  name: string
  unit: string
  value: number
}

export interface Prompt {
  name: string
  default: string
  override: string | null
}

export interface Report {
  id: number
  time: number
  alerts: (AlertRule & { measured: number })[]
  report: {
    title: string
    severity: 'info' | 'warning' | 'critical'
    summary: string
    findings: string[]
    actions: string[]
  }
}
