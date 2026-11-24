import operator
from time import time

OPS = {
  ">": operator.gt,
  "<": operator.lt,
  ">=": operator.ge,
  "<=": operator.le,
}

def _get_measured_value(data, path: str):
  parts = path.split(".")
  i = 0
  while i < len(parts):
    if isinstance(data, list):
      # names can contain dots (swarm tasks are svc.1.id), try the longest match first
      for j in range(len(parts), i, -1):
        match = next((d for d in data if d.get("name") == ".".join(parts[i:j])), None)
        if match is not None:
          data, i = match, j
          break
      else:
        data, i = data[int(parts[i])], i + 1  # by index, ValueError if not a number
    else:
      data, i = data[parts[i]], i + 1
  return data

PREFIX = "metric:"


def rule_key(rule: dict) -> str:
  # editing a threshold makes it a new rule with fresh state
  return f"{PREFIX}{rule['path']} {rule['op']} {rule['value']}"


def check_alerts(metrics, alerts, state, now: float | None = None) -> dict[str, list[dict]]:
  """Feed every rule through `state`, return the rules that changed, grouped by transition."""
  now = time() if now is None else now
  out = {"fired": [], "resolved": [], "remind": []}
  for alert in alerts:
      try:
          actual = _get_measured_value(metrics, alert["path"])
      except (KeyError, IndexError, ValueError, TypeError):
          continue  # not reporting (stopped container?), leave its state alone
      if not isinstance(actual, int | float):  # path points at something that isn't a number
          continue

      key = rule_key(alert)
      breached = OPS[alert["op"]](actual, alert["value"])  # comparing values, see OPS
      since = state.since(key)
      transition = state.update(key, breached, now, alert.get("remind", 0) * 60)  # minutes in config
      if transition:
          out[transition].append({**alert, "measured": actual, "since": since if since is not None else now})
  state.prune(PREFIX, {rule_key(a) for a in alerts})
  return out
