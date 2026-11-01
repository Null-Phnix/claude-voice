"""setup/uninstall must never overwrite an unparseable ~/.claude/settings.json."""

import json
import os
import tempfile
import unittest
from unittest import mock

import claude_voice as cv


class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.settings = os.path.join(self.home, ".claude", "settings.json")
        self.command = os.path.join(self.home, ".claude", "commands", "voice.md")
        for name, value in (("SETTINGS_PATH", self.settings), ("COMMAND_PATH", self.command)):
            patcher = mock.patch.object(cv, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        os.makedirs(os.path.dirname(self.settings))

    def _write(self, text: str):
        with open(self.settings, "w") as f:
            f.write(text)

    def _read(self) -> str:
        with open(self.settings) as f:
            return f.read()

    def test_missing_file_loads_as_empty(self):
        self.assertEqual(cv._load_settings(), {})

    def test_malformed_json_raises(self):
        self._write('{"permissions": {"allow": ["Bash"]},}')  # trailing comma
        with self.assertRaises(cv.SettingsError):
            cv._load_settings()

    def test_non_object_raises(self):
        self._write("[]")
        with self.assertRaises(cv.SettingsError):
            cv._load_settings()

    def test_setup_refuses_to_overwrite_malformed_settings(self):
        broken = '{"model": "opus", "permissions": {"allow": ["Bash"]},}'
        self._write(broken)
        with mock.patch.object(cv, "load_config", return_value=cv.default_config()), \
             mock.patch.object(cv, "save_config"), \
             mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                cv.cmd_setup()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(self._read(), broken)
        self.assertFalse(os.path.exists(self.command))

    def test_uninstall_refuses_to_overwrite_malformed_settings(self):
        broken = '{"hooks": {"Stop": [{"hooks": [{"command": "claude-voice"}]}]},'
        self._write(broken)
        os.makedirs(os.path.dirname(self.command))
        with open(self.command, "w") as f:
            f.write("stub")
        with mock.patch.object(cv, "_daemon_alive", return_value=False), mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                cv.cmd_uninstall()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(self._read(), broken)
        self.assertTrue(os.path.exists(self.command))

    def test_setup_then_uninstall_preserves_other_settings(self):
        original = {
            "model": "opus",
            "permissions": {"allow": ["Bash(git:*)"]},
            "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}]},
        }
        self._write(json.dumps(original))
        with mock.patch.object(cv, "load_config", return_value=cv.default_config()), \
             mock.patch.object(cv, "save_config"), \
             mock.patch.object(cv, "_invoker", return_value="/usr/bin/claude-voice"), \
             mock.patch("builtins.print"):
            cv.cmd_setup()
            after_setup = json.loads(self._read())
            self.assertEqual(after_setup["model"], "opus")
            self.assertEqual(after_setup["permissions"], original["permissions"])
            self.assertEqual(len(after_setup["hooks"]["Stop"]), 2)
            self.assertTrue(os.path.exists(self.command))

            with mock.patch.object(cv, "_daemon_alive", return_value=False):
                cv.cmd_uninstall()
        self.assertEqual(json.loads(self._read()), original)
        self.assertFalse(os.path.exists(self.command))

    def test_status_tolerates_malformed_settings(self):
        self._write("{not json")
        self.assertFalse(cv._hook_installed())

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        cv._save_settings({"a": 1})
        self.assertEqual(json.loads(self._read()), {"a": 1})
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.settings))), ["settings.json"])


if __name__ == "__main__":
    unittest.main()
