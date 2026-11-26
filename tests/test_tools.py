import json
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


class ToolTests(unittest.IsolatedAsyncioTestCase):
  async def test_shell_refuses_without_root(self):
    with patch("agent.tools.os.geteuid", return_value=1000):
      self.assertIn("disabled", await tools.run_command("id"))

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

  def test_sweep_kills_only_the_agent_uid(self):
    def proc(uid):
      return Mock(info={"uids": Mock(real=uid)})
    agent, other, unreadable = proc(tools.AGENT_UID), proc(1000), Mock(info={"uids": None})
    with patch("agent.tools.psutil.process_iter", return_value=[agent, other, unreadable]):
      tools._kill_agent_processes()
    agent.kill.assert_called_once()
    other.kill.assert_not_called()
    unreadable.kill.assert_not_called()

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
