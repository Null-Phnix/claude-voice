"""Prompt submit: pause the prompting terminal's speech and prewarm the daemon."""

import unittest
from unittest import mock

import claude_voice as cv


class ClaimHookTests(unittest.TestCase):
    def run_claim(self, payload, alive=True):
        sent = []
        with (
            mock.patch.object(cv, "_read_hook_json", return_value=payload),
            mock.patch.object(cv, "load_config", return_value=cv.default_config()),
            mock.patch.object(cv, "_daemon_alive", return_value=alive),
            mock.patch.object(cv, "_spawn_daemon") as spawn,
            mock.patch.object(cv, "_resolve_tty", return_value="/dev/pts/9"),
            mock.patch.object(cv, "_send_to_daemon", side_effect=lambda p, **k: sent.append(p)),
        ):
            cv.cmd_claim()
        return sent, spawn

    def test_prompt_claims_and_asks_to_pause_same_terminal(self):
        sent, _ = self.run_claim({"prompt": "next question"})
        self.assertEqual(sent, [{"op": "claim", "tty_path": "/dev/pts/9", "pause": "same"}])

    def test_voice_command_does_not_pause(self):
        sent, _ = self.run_claim({"prompt": "/voice resume"})
        self.assertEqual(sent, [])

    def test_cold_daemon_is_prewarmed(self):
        sent, spawn = self.run_claim({"prompt": "hi"}, alive=False)
        spawn.assert_called_once()
        self.assertEqual(sent, [])


class PauseModeTests(unittest.TestCase):
    def claim(self, tty_path, mode):
        player = mock.Mock()
        player.pause.return_value = True
        conn = mock.Mock()
        conn.recv.side_effect = [
            (cv.json.dumps({"op": "claim", "tty_path": tty_path, "pause": mode}) + "\n").encode(), b""]
        with mock.patch.multiple(cv, _player=player, _speaking_tty="/dev/pts/1",
                                 _active_tty=None, _active_tty_t=0.0,
                                 _daemon_log=mock.DEFAULT):
            cv._handle_client(conn)
        return player.pause.called

    def test_same_mode_pauses_only_the_speaking_terminal(self):
        self.assertTrue(self.claim("/dev/pts/1", "same"))
        self.assertFalse(self.claim("/dev/pts/2", "same"))

    def test_any_mode_pauses_every_terminal(self):
        self.assertTrue(self.claim("/dev/pts/2", "any"))

    def test_off_mode_never_pauses(self):
        self.assertFalse(self.claim("/dev/pts/1", "off"))


if __name__ == "__main__":
    unittest.main()
