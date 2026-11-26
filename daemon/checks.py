import asyncio
import os
import signal
import subprocess
import time

HOST = "/host" if os.path.isdir("/host") else "/"
# user commands run on the host as this uid. with `pid: host` it can signal any host
# process with the same uid, so it's deliberately one nothing else uses
UID = 2_100_000_000
TIMEOUT = 120  # seconds
MAX_OUTPUT = 20_000  # chars
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def run_command(argv: list[str]) -> tuple[int | None, str]:
  proc = subprocess.Popen(
    ["chroot", f"--userspec={UID}:{UID}", f"--groups={UID}", HOST, *argv],
    env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/", "LANG": "C.UTF-8"},
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
    start_new_session=True,
  )
  try:
    output, _ = proc.communicate(timeout=TIMEOUT)
    code = proc.returncode
  except subprocess.TimeoutExpired:
    os.killpg(proc.pid, signal.SIGKILL)
    try:
      output, _ = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:  # something outside the group still holds the output open
      output = ""
    output += f"\n[timed out after {TIMEOUT}s]"
    code = None
  return code, output[-MAX_OUTPUT:]


async def run_due_checks(commands: list[dict], results: dict):
  now = time.time()
  due = []
  for c in commands:
    every = c.get("every")
    if not every:
      continue
    last = results.get(c["name"])
    if last is None or now - last["ran_at"] >= int(every[:-1]) * UNITS[every[-1]]:
      due.append(c)

  outcomes = await asyncio.gather(*(asyncio.to_thread(run_command, c["command"]) for c in due))
  for c, (code, output) in zip(due, outcomes):
    results[c["name"]] = {"exit_code": code, "output": output, "ran_at": now}


def check_rules(commands: list[dict], results: dict) -> list[dict]:
  # a failing check is an alert rule like any other, so it fires and resolves the same way
  rules = []
  for c in commands:
    if c["name"] in results:
      rules.append({
        "path": f"checks.{c['name']}.exit_code", "op": "!=", "value": 0,
        "cooldown": c.get("cooldown", 0), "output": results[c["name"]]["output"],
      })
  return rules
