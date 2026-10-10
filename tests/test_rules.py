"""Per-session and per-project rules for which sessions speak, in which voice."""

import os
import tempfile
import time
import unittest
from unittest import mock

import claude_voice as cv


class VoiceRuleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        os.makedirs(os.path.join(self.root, "proj", "sub"))
        patcher = mock.patch.object(cv, "SESSIONS_PATH", os.path.join(self.root, "sessions.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = dict(cv.default_config(), projects={
            self.root: {"voice": "am_adam"},
            os.path.join(self.root, "proj"): {"enabled": False, "voice": "bf_emma"},
        })

    def test_default_applies_without_rules(self):
        rule = cv.voice_rule(dict(self.cfg, projects={}), None, self.root)
        self.assertTrue(rule["enabled"])
        self.assertIsNone(rule["voice"])

    def test_longest_project_prefix_wins(self):
        rule = cv.voice_rule(self.cfg, None, os.path.join(self.root, "proj", "sub"))
        self.assertFalse(rule["enabled"])
        self.assertEqual(rule["voice"], "bf_emma")

    def test_prefix_does_not_match_sibling_directory_name(self):
        rule = cv.voice_rule(dict(self.cfg, default_enabled=False), None, self.root + "-other")
        self.assertFalse(rule["enabled"])
        self.assertEqual(rule["source"], "default")

    def test_session_override_beats_project(self):
        cv._save_sessions({"s1": {"enabled": True, "voice": "bm_george", "t": time.time()}})
        rule = cv.voice_rule(self.cfg, "s1", os.path.join(self.root, "proj"))
        self.assertTrue(rule["enabled"])
        self.assertEqual(rule["voice"], "bm_george")

    def test_stale_session_overrides_expire(self):
        cv._save_sessions({"s1": {"enabled": True, "t": time.time() - cv.SESSION_TTL - 1}})
        rule = cv.voice_rule(self.cfg, "s1", os.path.join(self.root, "proj"))
        self.assertFalse(rule["enabled"])


if __name__ == "__main__":
    unittest.main()
