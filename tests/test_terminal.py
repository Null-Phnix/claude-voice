"""Hooks run without a controlling terminal; find Claude Code's instead."""

import os
from types import SimpleNamespace
import unittest
from unittest import mock

import claude_voice as cv


class AncestorTtyTests(unittest.TestCase):
    def test_claude_pid_terminal_is_used(self):
        links = {f"/proc/4242/fd/{fd}": "/dev/pts/7" for fd in (0, 1, 2)}

        def readlink(path):
            if path in links:
                return links[path]
            raise OSError(path)

        with (
            mock.patch.dict(os.environ, {"CLAUDE_PID": "4242"}),
            mock.patch.object(cv.os, "readlink", side_effect=readlink),
        ):
            self.assertEqual(cv._ancestor_tty(), "/dev/pts/7")

    def test_pipes_and_files_are_not_terminals(self):
        with (
            mock.patch.dict(os.environ, {"CLAUDE_PID": "4242"}),
            mock.patch.object(cv.os, "readlink", return_value="pipe:[123]"),
            mock.patch.object(cv.os, "getppid", return_value=1),
        ):
            self.assertIsNone(cv._ancestor_tty())

    def test_resolve_tty_falls_back_to_ancestor(self):
        with (
            mock.patch.object(cv.os, "ttyname", side_effect=OSError),
            mock.patch.object(cv, "_ancestor_tty", return_value="/dev/pts/3"),
        ):
            self.assertEqual(cv._resolve_tty(), "/dev/pts/3")


class RenderSwitchTests(unittest.TestCase):
    def test_render_false_never_touches_the_terminal(self):
        from test_playback import FakeStream, fake_sd

        FakeStream.instances = []
        cfg = dict(cv.default_config(), chime=False, done_pause=0)
        samples = cv.np.zeros(cv.SAMPLE_RATE // 10, dtype=cv.np.float32)

        def pipe(sentence, **kwargs):
            yield SimpleNamespace(audio=SimpleNamespace(numpy=lambda: samples))

        with (
            mock.patch.object(cv, "sd", fake_sd()),
            mock.patch.object(cv, "load_config", return_value=cfg),
            mock.patch.object(cv, "get_pipe", return_value=pipe),
            mock.patch.object(cv, "get_tty") as get_tty,
            mock.patch.object(cv, "_start_keypress_listener") as listener,
        ):
            stats = cv.speak_and_highlight("Hello there.", provider="kokoro",
                                           record_history=False, render=False)
        get_tty.assert_not_called()
        listener.assert_not_called()
        self.assertTrue(stats["finished"])


if __name__ == "__main__":
    unittest.main()
