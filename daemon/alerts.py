import operator
from time import time

OPS = {
  ">": operator.gt,
  "<": operator.lt,
  ">=": operator.ge,
  "<=": operator.le,
  "==": operator.eq,
}

def _get_measured_value(data, path: str):
  for part in path.split("."):
    if isinstance(data, list):
      if part.isdigit():
        data = data[int(part)]
      else:
        data = next(d for d in data if d.get("name") == part)
    else:
      data = data[part]
  return data

def check_alerts(metrics, alerts, last_fired: dict) -> list[dict]:
  now = time()
  active = []
  for alert in alerts:
      try:
          actual = _get_measured_value(metrics, alert["path"])
      except (KeyError, IndexError, StopIteration):
          continue
      if not OPS[alert["op"]](actual, alert["value"]): # comparing values, see OPS
          continue

      cooldown = alert.get("cooldown", 0) * 60  # minutes in config, need seconds
      key = alert["path"]
      if key in last_fired and (now - last_fired[key]) < cooldown:
          continue

      last_fired[key] = now
      active.append(alert)
  return active
