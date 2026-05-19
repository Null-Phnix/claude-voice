"""Multi-terminal routing tests for the claude-voice daemon.

Boots the daemon with a stubbed Kokoro pipeline and exercises the claim,
unclaim, mute, unmute, and toggle ops, plus the active-tty and mute
gating that the speak op applies before generating audio.

Run with:
    python -m unittest tests.test_routing -v
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(sock_path):
            resp = _send(sock_path, {"op": "ping"}, timeout=0.5)
            if resp and resp.get("ok"):
                return True
        time.sleep(0.1)
    return False


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="cv-routing-test-")
        cls.sock_path = os.path.join(cls._tmp, "daemon.sock")
        cls.log_path = os.path.join(cls._tmp, "daemon.log")

        env = dict(os.environ)
        env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"

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
        _send(cls.sock_path, {"op": "shutdown"}, timeout=1.0)
        try:
            cls.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=2)

    def setUp(self):
        # Reset state between tests so order doesn't matter.
        _send(self.sock_path, {"op": "unclaim"})
        _send(self.sock_path, {"op": "unmute", "tty_path": "/dev/ttysA"})
        _send(self.sock_path, {"op": "unmute", "tty_path": "/dev/ttysB"})

    # ── claim / unclaim ────────────────────────────────────────────

    def test_claim_records_active_tty(self):
        resp = _send(self.sock_path, {"op": "claim", "tty_path": "/dev/ttysA"})
        self.assertEqual(resp, {"ok": True, "active_tty": "/dev/ttysA"})
        status = _send(self.sock_path, {"op": "status"})
        self.assertEqual(status.get("active_tty"), "/dev/ttysA")

    def test_unclaim_clears_active_tty(self):
        _send(self.sock_path, {"op": "claim", "tty_path": "/dev/ttysA"})
        _send(self.sock_path, {"op": "unclaim"})
        status = _send(self.sock_path, {"op": "status"})
        self.assertIsNone(status.get("active_tty"))

    def test_speak_from_non_active_tty_is_skipped(self):
        _send(self.sock_path, {"op": "claim", "tty_path": "/dev/ttysA"})
        resp = _send(
            self.sock_path,
            {"op": "speak", "text": "hello", "tty_path": "/dev/ttysB"},
        )
        self.assertEqual(resp, {"skipped": "not_active_tty"})

    def test_speak_from_active_tty_is_queued(self):
        _send(self.sock_path, {"op": "claim", "tty_path": "/dev/ttysA"})
        resp = _send(
            self.sock_path,
            {"op": "speak", "text": "hello", "tty_path": "/dev/ttysA"},
        )
        self.assertEqual(resp.get("queued"), True)

    def test_speak_with_no_claim_is_queued(self):
        resp = _send(
            self.sock_path,
            {"op": "speak", "text": "hello", "tty_path": "/dev/ttysA"},
        )
        self.assertEqual(resp.get("queued"), True)

    # ── mute / unmute / toggle ─────────────────────────────────────

    def test_mute_then_speak_is_skipped(self):
        _send(self.sock_path, {"op": "mute", "tty_path": "/dev/ttysA"})
        resp = _send(
            self.sock_path,
            {"op": "speak", "text": "hi", "tty_path": "/dev/ttysA"},
        )
        self.assertEqual(resp, {"skipped": "muted"})

    def test_mute_beats_claim(self):
        # A tty muted *and* holding the active claim should still be silent.
        _send(self.sock_path, {"op": "claim", "tty_path": "/dev/ttysA"})
        _send(self.sock_path, {"op": "mute", "tty_path": "/dev/ttysA"})
        resp = _send(
            self.sock_path,
            {"op": "speak", "text": "hi", "tty_path": "/dev/ttysA"},
        )
        self.assertEqual(resp, {"skipped": "muted"})

    def test_toggle_flips_state(self):
        a = _send(self.sock_path, {"op": "toggle", "tty_path": "/dev/ttysA"})
        b = _send(self.sock_path, {"op": "toggle", "tty_path": "/dev/ttysA"})
        self.assertEqual(a.get("muted"), True)
        self.assertEqual(b.get("muted"), False)

    def test_mute_without_tty_path_is_rejected(self):
        resp = _send(self.sock_path, {"op": "mute"})
        self.assertEqual(resp.get("error"), "no tty_path")

    def test_status_reports_muted_list(self):
        _send(self.sock_path, {"op": "mute", "tty_path": "/dev/ttysA"})
        _send(self.sock_path, {"op": "mute", "tty_path": "/dev/ttysB"})
        status = _send(self.sock_path, {"op": "status"})
        self.assertEqual(status.get("muted_ttys"), ["/dev/ttysA", "/dev/ttysB"])


if __name__ == "__main__":
    unittest.main()
