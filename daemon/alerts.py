import operator

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

RESOLVE_AFTER = 60  # seconds a rule has to stay clear before it resolves, stops flapping


def check_alerts(metrics, rules, firing: dict, now: float):
  """Updates `firing` in place, returns the alerts that fired, resolved and need a reminder."""
  fired, resolved, reminders = [], [], []
  keys = set()
  for rule in rules:
    key = f"{rule['path']} {rule['op']} {rule['value']}"  # editing a threshold starts fresh
    keys.add(key)
    try:
      value = _get_measured_value(metrics, rule["path"])
    except (KeyError, IndexError, ValueError, TypeError):
      continue  # not reporting right now (stopped container?), keep its state
    if not isinstance(value, int | float):
      continue

    alert = {**rule, "measured": value}
    state = firing.get(key)
    if OPS[rule["op"]](value, rule["value"]):
      if state is None:
        firing[key] = {"since": now, "notified": now, "clear_since": None}
        fired.append(alert)
        continue
      state["clear_since"] = None
      if rule.get("remind") and now - state["notified"] >= rule["remind"] * 60:
        state["notified"] = now
        reminders.append({**alert, "since": state["since"]})
    elif state is not None:
      if state["clear_since"] is None:
        state["clear_since"] = now
      if now - state["clear_since"] >= RESOLVE_AFTER:
        del firing[key]
        resolved.append({**alert, "since": state["since"]})

  for key in firing.keys() - keys:
    del firing[key]  # rule was deleted or edited
  return fired, resolved, reminders
