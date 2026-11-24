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

RESOLVE_AFTER = 60  # seconds something has to stay fine before it resolves, stops flapping
DOCKER = "docker:"  # key prefix for container problems
CLEAN_EXITS = {0, 137, 143}  # what a normal `docker stop` leaves behind


def _step(firing: dict, key: str, breached: bool, now: float, remind: float = 0) -> str | None:
  """Moves one key through ok -> firing -> resolved. Returns fired, remind, resolved or None."""
  state = firing.get(key)
  if breached:
    if state is None:
      firing[key] = {"since": now, "notified": now, "clear_since": None}
      return "fired"
    state["clear_since"] = None
    if remind and now - state["notified"] >= remind:
      state["notified"] = now
      return "remind"
  elif state is not None:
    if state["clear_since"] is None:
      state["clear_since"] = now
    if now - state["clear_since"] >= RESOLVE_AFTER:
      del firing[key]
      return "resolved"
  return None


def check_alerts(metrics, rules, firing: dict, now: float):
  """Updates `firing` in place, returns the alerts that fired, resolved and need a reminder."""
  out = {"fired": [], "resolved": [], "remind": []}
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
    since = firing.get(key, {}).get("since", now)
    happened = _step(firing, key, OPS[rule["op"]](value, rule["value"]), now, rule.get("remind", 0) * 60)
    if happened:
      out[happened].append({**rule, "measured": value, "since": since})

  for key in firing.keys() - keys:
    if not key.startswith(DOCKER):
      del firing[key]  # rule was deleted or edited
  return out["fired"], out["resolved"], out["remind"]


def check_containers(containers: list[dict], firing: dict, restarts: dict, started: float, now: float):
  """Same idea for containers, by name: crashed, restarting, unhealthy. Returns fired and resolved.

  `restarts` remembers each container's restart count between polls, `started` is when ember started.
  """
  out = {"fired": [], "resolved": []}
  keys = set()
  for c in containers:
    name = c["name"]
    crashed_key = f"{DOCKER}{name} crashed"
    problems = {
      # with always/unless-stopped, docker restarts real crashes, so "exited" means someone stopped it.
      # anything that died before ember started isn't news, unless we were already tracking it
      "crashed": c["status"] == "exited" and c["policy"] not in ("always", "unless-stopped")
                 and (c["oom"] or c["exit_code"] not in CLEAN_EXITS)
                 and (c["finished"] >= started or crashed_key in firing),
      "restarting": c["status"] == "restarting" or c["restarts"] > restarts.get(name, c["restarts"]),
      "unhealthy": c["status"] == "running" and c["health"] == "unhealthy",
    }
    for problem, breached in problems.items():
      key = f"{DOCKER}{name} {problem}"
      keys.add(key)
      since = firing.get(key, {}).get("since", now)
      happened = _step(firing, key, breached, now)
      if happened:
        out[happened].append({"source": "docker", "problem": problem, **c, "since": since})

  restarts.clear()
  restarts.update({c["name"]: c["restarts"] for c in containers})
  for key in [k for k in firing if k.startswith(DOCKER) and k not in keys]:
    del firing[key]  # container was removed
  return out["fired"], out["resolved"]
