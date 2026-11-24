import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from docker.errors import DockerException, NotFound

from agent.tools import AGENT_GID, AGENT_UID, CLEAN_SHELL_ENV, MAX_TIMEOUT, _timeout, _truncate, build_tools


def _tools(cfg: dict | None = None) -> dict:
    with patch("agent.tools.logger"):
        return {fn.__name__: fn for fn in build_tools(cfg or {"agent": {"shell": {"cwd": "."}}})}


# real subprocess tests: as root, run_command drops to UID 10001, which can't use the temp dirs
needs_non_root = unittest.skipIf(os.geteuid() == 0, "run as non-root")


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (ProcessLookupError, FileNotFoundError):
        return False
    return state != "Z"


async def _read_pid(pid_file: Path) -> int:
    for _ in range(100):
        if pid_file.exists() and (text := pid_file.read_text().strip()):
            return int(text)
        await asyncio.sleep(0.02)
    raise AssertionError(f"{pid_file} never written")


async def _dies(pid: int) -> bool:
    for _ in range(100):
        if not _is_alive(pid):
            return True
        await asyncio.sleep(0.02)
    return False


def _docker_client(**containers) -> Mock:
    client = Mock()
    for method, value in containers.items():
        setattr(getattr(client.containers, method), "side_effect" if isinstance(value, Exception) else "return_value", value)
    return client


class HelperTests(unittest.TestCase):
    def test_truncate_keeps_tail(self):
        self.assertEqual(_truncate("short", 10), "short")
        self.assertEqual(_truncate("0123456789abcdef", 6), "[truncated]\nabcdef")

    def test_timeout_falls_back_or_caps(self):
        for raw, expected in [(None, 30), (0, 30), (-5, 30), (float("nan"), 30), (5, 5), (float("inf"), MAX_TIMEOUT)]:
            with self.subTest(raw=raw):
                self.assertEqual(_timeout(raw, 30), expected)


@needs_non_root
class RunCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_captures_stdout_stderr_and_exit_code(self):
        output = await _tools()["run_command"]("echo hello; echo oops >&2; exit 3")
        self.assertEqual(output, "[stdout]\nhello\n\n[stderr]\noops\n\n[exit code: 3]")

    async def test_uses_clean_env(self):
        with patch.dict(os.environ, {"SUPER_SECRET_API_KEY": "sk-secret"}):
            output = await _tools()["run_command"]("env")
        self.assertNotIn("sk-secret", output)
        self.assertIn("HOME=/tmp", output)

    async def test_caps_output_keeping_tail(self):
        run_command = _tools({"agent": {"shell": {"cwd": ".", "max_output_chars": 40}}})["run_command"]
        output = await run_command("printf HEAD; head -c 50000 /dev/zero | tr '\\0' X; echo TAIL")
        self.assertTrue(output.startswith("[truncated]"))
        self.assertIn("TAIL", output)
        self.assertNotIn("HEAD", output)

    async def test_bad_cwd_returns_shell_error(self):
        run_command = _tools({"agent": {"shell": {"cwd": "/nonexistent-ember-dir"}}})["run_command"]
        with self.assertLogs("agent.tools", level="WARNING"):
            self.assertTrue((await run_command("echo hi")).startswith("Shell error:"))

    async def test_timeout_kills_whole_process_group(self):
        cases = {
            "waited child": "sleep 30 & echo $! > {pid}; wait",
            "child outliving sh": "sleep 30 & echo $! > {pid}",
            "TERM-trapping grandchild": "(trap '' TERM; sleep 30 & echo $! > {pid}; wait) & wait",
        }
        for name, cmd in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmpdir:
                pid_file = Path(tmpdir) / "pid"
                output = await _tools()["run_command"](cmd.format(pid=pid_file), timeout_seconds=0.3)
                self.assertEqual(output, "[Command timed out after 0.3s]")
                self.assertTrue(await _dies(await _read_pid(pid_file)))

    async def test_normal_exit_kills_leftover_background_children(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "pid"
            output = await _tools()["run_command"](f"sleep 30 >/dev/null 2>&1 & echo $! > {pid_file}")
            self.assertEqual(output, "[exit code: 0]")
            self.assertTrue(await _dies(await _read_pid(pid_file)))

    async def test_cancel_kills_process_group(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "pid"
            task = asyncio.create_task(_tools()["run_command"](f"sleep 30 & echo $! > {pid_file}; wait"))
            pid = await _read_pid(pid_file)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(await _dies(pid))


class PrivilegeDropTests(unittest.IsolatedAsyncioTestCase):
    async def test_drops_privileges_only_as_root(self):
        empty = Mock(read=AsyncMock(return_value=b""))
        proc = Mock(pid=999_999_999, returncode=0, stdout=empty, stderr=empty, wait=AsyncMock(return_value=0))
        spawn = AsyncMock(return_value=proc)
        with patch("agent.tools.asyncio.create_subprocess_exec", spawn), patch("agent.tools.os.killpg"):
            with patch("agent.tools.os.geteuid", return_value=0):
                await _tools()["run_command"]("id")
            kwargs = spawn.call_args.kwargs
            self.assertEqual((kwargs["user"], kwargs["group"], kwargs["extra_groups"]), (AGENT_UID, AGENT_GID, []))
            self.assertEqual(kwargs["env"], CLEAN_SHELL_ENV)
            self.assertTrue(kwargs["start_new_session"])

            with patch("agent.tools.os.geteuid", return_value=1000), self.assertLogs("agent.tools", "WARNING"):
                run_command = build_tools({"agent": {"shell": {"cwd": "."}}})[0]
            await run_command("id")
            self.assertNotIn("user", spawn.call_args.kwargs)


class DockerToolsTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, client, tool, *args, cfg=None, **kwargs):
        with patch("agent.tools.docker.from_env", return_value=client):
            result = await _tools(cfg)[tool](*args, **kwargs)
        client.close.assert_called_once()
        return result

    async def test_docker_list(self):
        c = Mock(id="abcdef1234567890", status="running", attrs={
            "State": {"Health": {"Status": "healthy"}}, "RestartCount": 2, "Config": {"Image": "nginx:1.27"},
        })
        c.name = "web"
        client = _docker_client(list=[c])
        result = json.loads(await self._call(client, "docker_list"))
        client.containers.list.assert_called_once_with(all=True, ignore_removed=True)
        self.assertEqual(result, [{
            "name": "web", "id": "abcdef123456", "status": "running",
            "health": "healthy", "restart_count": 2, "image": "nginx:1.27",
        }])

    async def test_docker_errors(self):
        result = await self._call(_docker_client(list=DockerException("daemon down")), "docker_list")
        self.assertEqual(result, "Docker error: daemon down")
        result = await self._call(_docker_client(get=NotFound("nope")), "docker_inspect", "missing")
        self.assertEqual(result, "Container not found: missing")

    async def test_docker_inspect_never_leaks_env_values(self):
        c = Mock(id="fedcba9876543210", attrs={
            "Config": {"Image": "postgres:16", "Env": ["POSTGRES_PASSWORD=hunter2", "TOKEN=a=b", "FLAG"]},
            "State": {"Status": "running", "Health": {"Status": "healthy", "Log": [{"Output": "secret"}]}},
        })
        c.name = "db"
        raw = await self._call(_docker_client(get=c), "docker_inspect", "db")
        for secret in ("hunter2", "a=b", "secret"):
            self.assertNotIn(secret, raw)
        data = json.loads(raw)
        self.assertEqual(data["env_keys"], ["POSTGRES_PASSWORD", "TOKEN", "FLAG"])
        self.assertEqual(data["state"]["Health"], "healthy")

    async def test_docker_logs_clamps_and_truncates(self):
        c = Mock()
        c.logs.return_value = b"old-prefix\xffline-end"
        client = _docker_client(get=c)
        cfg = {"agent": {"shell": {"max_output_chars": 9}}}
        result = await self._call(client, "docker_logs", "web", cfg=cfg, tail=99999, since_minutes=15)
        self.assertEqual(result, "[truncated]\n�line-end")
        self.assertEqual(c.logs.call_args.kwargs["tail"], 2000)
        self.assertIsInstance(c.logs.call_args.kwargs["since"], datetime)

        client.close.reset_mock()
        await self._call(client, "docker_logs", "web", tail=0, since_minutes=10**12)
        self.assertEqual(c.logs.call_args.kwargs["tail"], 1)
        self.assertIsInstance(c.logs.call_args.kwargs["since"], datetime)
