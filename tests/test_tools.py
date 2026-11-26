import io
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from docker.errors import DockerException, NotFound

from agent import tools


def container(name="web"):
  c = Mock(status="running", attrs={
    "State": {"Status": "running", "Health": {"Status": "healthy"}},
    "Config": {"Image": "nginx", "Env": ["PASSWORD=hunter2", "TZ=UTC"]},
    "HostConfig": {"RestartPolicy": {"Name": "always"}, "Memory": 0, "NanoCpus": 0},
    "RestartCount": 3,
    "Mounts": [{"Source": "/srv", "Destination": "/data"}],
  })
  c.name = name
  return c


class ToolTests(unittest.TestCase):
  def test_largest_dirs_sorts_by_size(self):
    with tempfile.TemporaryDirectory() as host:
      for name, size in [("small", 10_000), ("big", 3_000_000)]:
        os.makedirs(f"{host}/srv/{name}/nested")
        with open(f"{host}/srv/{name}/nested/file", "wb") as f:
          f.write(os.urandom(size))
      with patch("agent.tools.HOST", host):
        out = tools.largest_dirs("/srv").splitlines()
        missing = tools.largest_dirs("/nope")
    self.assertTrue(out[0].endswith("/srv/big") and out[1].endswith("/srv/small"))
    self.assertIn("Can't read /nope", missing)

  def test_top_processes_names_the_container(self):
    procs = []
    for pid, cpu in [(1, 5.0), (2, 50.0)]:
      p = Mock(pid=pid, info={"name": f"proc{pid}"}, **{"cpu_percent.return_value": cpu})
      p.memory_info.return_value.rss = 2**20
      procs.append(p)
    cid = "a" * 64
    cgroups = {"/proc/1/cgroup": "0::/system.slice/sshd.service", "/proc/2/cgroup": f"0::/../docker-{cid}.scope"}
    with patch("agent.tools.psutil.process_iter", return_value=procs), \
         patch("agent.tools.time.sleep"), \
         patch("agent.tools._container_names", return_value={cid: "web"}), \
         patch("agent.tools.open", lambda path: io.StringIO(cgroups[path]), create=True):
      rows = json.loads(tools.top_processes())
    self.assertEqual([(r["name"], r["container"]) for r in rows], [("proc2", "web"), ("proc1", None)])

  def test_inspect_hides_env_values(self):
    client = Mock(**{"containers.get.return_value": container()})
    with patch("agent.tools.docker.from_env", return_value=client):
      out = json.loads(tools.docker_inspect("web"))
    self.assertEqual(out["env"], ["PASSWORD", "TZ"])
    self.assertNotIn("hunter2", json.dumps(out))
    client.close.assert_called_once()

  def test_unknown_container_tells_the_agent(self):
    client = Mock(**{"containers.get.side_effect": NotFound("gone")})
    with patch("agent.tools.docker.from_env", return_value=client):
      self.assertIn("Docker error", tools.docker_logs("nope"))
      self.assertIn("Docker error", tools.docker_inspect("nope"))

  def test_unreachable_daemon_tells_the_agent(self):
    with patch("agent.tools.docker.from_env", side_effect=DockerException("no daemon")):
      self.assertIn("Docker error", tools.docker_list())

  def test_list_and_logs(self):
    c = container()
    c.logs.return_value = b"line one\n"
    client = Mock(**{"containers.list.return_value": [c], "containers.get.return_value": c})
    with patch("agent.tools.docker.from_env", return_value=client):
      rows = json.loads(tools.docker_list())
      logs = tools.docker_logs("web", lines=99999)
    self.assertEqual(rows[0]["health"], "healthy")
    self.assertEqual(logs, "line one\n")
    c.logs.assert_called_with(tail=2000, timestamps=True)

  def test_long_output_keeps_the_end(self):
    out = tools._truncate("x" * tools.MAX_OUTPUT + "END")
    self.assertTrue(out.startswith("[truncated]") and out.endswith("END"))
