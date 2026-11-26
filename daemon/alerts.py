import operator
from time import time

OPS = {
  ">": operator.gt,
  "<": operator.lt,
  ">=": operator.ge,
  "<=": operator.le,
  "==": operator.eq,
  "!=": operator.ne,
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

def check_alerts(metrics, alerts, firing: set, last_fired: dict) -> tuple[list[dict], list[dict]]:
  now = time()
  fired, resolved = [], []
  for alert in alerts:
    key = f"{alert['path']} {alert['op']} {alert['value']}"
    try:
      actual = _get_measured_value(metrics, alert["path"])
    except (KeyError, IndexError, StopIteration):
      continue
    breached = OPS[alert["op"]](actual, alert["value"]) # comparing values, see OPS

    if breached and key not in firing:
      cooldown = alert.get("cooldown", 0) * 60  # minutes in config, need seconds
      if key in last_fired and now - last_fired[key] < cooldown:
        continue
      firing.add(key)
      last_fired[key] = now
      fired.append({**alert, "measured": actual})
    elif not breached and key in firing:
      firing.remove(key)
      resolved.append({**alert, "measured": actual, "since": last_fired[key]})
      last_fired[key] = now  # cooldown also counts from the resolve, so a short dip doesn't re-fire
  return fired, resolved
