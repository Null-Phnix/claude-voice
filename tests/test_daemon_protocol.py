"""Socket protocol tests for the claude-voice daemon.

These tests exercise the daemon's wire protocol without playing audio.
The Kokoro pipeline is patched out so the daemon can boot in <1s even
on CI without GPUs, sound devices, or 300MB of model weights.

Run with:
    pytest tests/test_daemon_protocol.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "claude_voice.py"


def _send(sock_path: str, payload: dict, timeout: float = 2.0) -> dict | None:
    """Send one JSON request to the daemon and return the parsed response."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock_path)
        s.sendall(json.dumps(payload).encode() + b"\n")
        buf = b""
        while b"\n" not in buf and len(buf) < 8192:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode("utf-8", errors="replace"))
    except (OSError, socket.timeout, json.JSONDecodeError):
        return None


def _wait_for_socket(sock_path: str, timeout: float = 10.0) -> bool:
    """Poll until the socket exists and accepts a ping, or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(sock_path):
            resp = _send(sock_path, {"op": "ping"}, timeout=0.5)
            if resp and resp.get("ok"):
                return True
        time.sleep(0.1)
    return False


class DaemonProtocolTests(unittest.TestCase):
    """Spawn a daemon with a stubbed pipeline, then drive it over the socket.

    Each test gets its own runtime dir so they can run in parallel without
    clobbering each other's sockets.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="cv-daemon-test-")
        cls.sock_path = os.path.join(cls._tmp, "daemon.sock")
        cls.log_path = os.path.join(cls._tmp, "daemon.log")

        # Stub kokoro and sounddevice before the daemon imports them, plus
        # override RUNTIME_DIR/SOCK_PATH so we don't fight with a real daemon.
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"

        # Bootstrap script: monkey-patch the heavy bits, then run cmd_daemon().
        bootstrap = (
            "import sys, types\n"
            "fake_kokoro = types.ModuleType('kokoro')\n"
            "class _KPipe:\n"
            "    def __init__(self, *a, **kw): pass\n"
            "    def __call__(self, *a, **kw): return []\n"
            "fake_kokoro.KPipeline = _KPipe\n"
            "sys.modules['kokoro'] = fake_kokoro\n"
            "import claude_voice\n"
            f"claude_voice.RUNTIME_DIR = {cls._tmp!r}\n"
            f"claude_voice.SOCK_PATH = {cls.sock_path!r}\n"
            f"claude_voice.PID_PATH = {os.path.join(cls._tmp, 'daemon.pid')!r}\n"
            f"claude_voice.LOG_PATH = {cls.log_path!r}\n"
            "claude_voice.cmd_daemon()\n"
        )

        cls.proc = subprocess.Popen(
            [sys.executable, "-c", bootstrap],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT),
        )

        if not _wait_for_socket(cls.sock_path, timeout=15):
            out, err = cls.proc.communicate(timeout=2)
            raise RuntimeError(
                f"daemon never came up.\nstdout: {out!r}\nstderr: {err!r}"
            )

    @classmethod
    def tearDownClass(cls):
        # Politely ask the daemon to shut down; force-kill if it doesn't.
        _send(cls.sock_path, {"op": "shutdown"}, timeout=1.0)
        try:
            cls.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=2)

    # ── individual ops ─────────────────────────────────────────────

    def test_ping(self):
        resp = _send(self.sock_path, {"op": "ping"})
        self.assertEqual(resp, {"ok": True})

    def test_status_reports_pid_and_idle(self):
        resp = _send(self.sock_path, {"op": "status"})
        self.assertIsNotNone(resp)
        self.assertTrue(resp.get("ok"))
        self.assertIsInstance(resp.get("pid"), int)
        self.assertIsInstance(resp.get("idle_s"), (int, float))

    def test_speak_empty_text_is_rejected(self):
        resp = _send(self.sock_path, {"op": "speak", "text": "  ", "tty_path": "/dev/null"})
        self.assertIsNotNone(resp)
        self.assertEqual(resp.get("error"), "empty")

    def test_speak_nonempty_text_is_queued(self):
        # The stubbed pipeline returns no audio, so playback no-ops harmlessly.
        resp = _send(
            self.sock_path,
            {"op": "speak", "text": "hello world", "tty_path": "/dev/null"},
        )
        self.assertIsNotNone(resp)
        self.assertEqual(resp.get("queued"), True)

    def test_malformed_json_does_not_crash_daemon(self):
        # Send garbage; daemon should respond with an error or just close,
        # and a subsequent ping must still succeed.
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(self.sock_path)
        s.sendall(b"not json at all\n")
        try:
            s.recv(256)
        except (OSError, socket.timeout):
            pass
        s.close()

        resp = _send(self.sock_path, {"op": "ping"})
        self.assertEqual(resp, {"ok": True})


if __name__ == "__main__":
    unittest.main()
