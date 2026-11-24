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

def check_alerts(metrics, alerts, last_fired: dict) -> list[dict]:
  now = time()
  active = []
  for alert in alerts:
      try:
          actual = _get_measured_value(metrics, alert["path"])
      except (KeyError, IndexError, ValueError, TypeError):
          continue
      if not isinstance(actual, int | float):  # path points at something that isn't a number
          continue
      if not OPS[alert["op"]](actual, alert["value"]): # comparing values, see OPS
          continue

      cooldown = alert.get("cooldown", 0) * 60  # minutes in config, need seconds
      key = alert["path"]
      if key in last_fired and (now - last_fired[key]) < cooldown:
          continue

      last_fired[key] = now
      active.append({**alert, "measured": actual})
  return active
