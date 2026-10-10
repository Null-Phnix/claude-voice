"""Streaming playback: start early, pause/resume/seek/stop on one cursor."""

import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

import claude_voice as cv


class FakeStream:
    """Drives the Player callback from a thread, like PortAudio would."""

    instances = []

    def __init__(self, samplerate, channels, dtype, callback):
        self.callback = callback
        self.frames = []
        self._run = threading.Event()
        self._closed = False
        FakeStream.instances.append(self)

    def start(self):
        self._run.set()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._run.is_set():
            out = np.zeros((4800, 1), dtype=np.float32)
            self.callback(out, 4800, None, None)
            self.frames.append(out[:, 0].copy())
            time.sleep(0.002)

    def stop(self):
        self._run.clear()

    abort = stop

    def close(self):
        self._closed = True


def fake_sd():
    return SimpleNamespace(OutputStream=FakeStream, play=lambda *a, **k: None,
                           wait=lambda: None, stop=lambda: None)


class PlayerTests(unittest.TestCase):
    def setUp(self):
        FakeStream.instances = []
        patcher = mock.patch.object(cv, "sd", fake_sd())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_plays_all_audio_then_drains_and_closes(self):
        p = cv.Player(24000)
        p.append(np.ones(24000, dtype=np.float32))
        p.finish_input()
        self.assertTrue(p.drained.wait(2))
        self.assertAlmostEqual(p.position, 1.0)
        time.sleep(0.1)
        self.assertTrue(FakeStream.instances[0]._closed)

    def test_pause_holds_position_and_resume_continues(self):
        p = cv.Player(24000)
        p.append(np.ones(24000 * 60, dtype=np.float32))
        time.sleep(0.02)
        self.assertTrue(p.pause())
        time.sleep(0.02)
        held = p.position
        time.sleep(0.05)
        self.assertEqual(p.position, held)
        p.resume()
        time.sleep(0.05)
        self.assertGreater(p.position, held)
        p.stop()

    def test_seek_moves_cursor(self):
        p = cv.Player(24000)
        p.append(np.ones(24000 * 60, dtype=np.float32))
        p.pause()
        self.assertEqual(p.seek(30.0), 30.0)
        self.assertFalse(p.paused)
        p.stop()

    def test_resume_after_finish_restarts(self):
        p = cv.Player(24000)
        p.append(np.ones(2400, dtype=np.float32))
        p.finish_input()
        self.assertTrue(p.drained.wait(2))
        p.resume()
        self.assertFalse(p.drained.is_set())
        self.assertTrue(p.drained.wait(2))

    def test_stop_cannot_pause(self):
        p = cv.Player(24000)
        p.append(np.ones(24000 * 60, dtype=np.float32))
        p.stop()
        self.assertFalse(p.pause())


class StreamingSpeakTests(unittest.TestCase):
    def setUp(self):
        FakeStream.instances = []

    def speak(self, text, slow_sentence_s=0.0, stop_after=None):
        synthesized = []

        def pipe(sentence, **kwargs):
            if synthesized:
                time.sleep(slow_sentence_s)
            synthesized.append(sentence)
            samples = np.full(len(sentence.split()) * 2400, 0.5, dtype=np.float32)
            yield SimpleNamespace(audio=SimpleNamespace(numpy=lambda: samples))

        cfg = dict(cv.default_config(), chime=False, done_pause=0)
        if stop_after is not None:
            threading.Timer(stop_after, lambda: cv._player.stop()).start()
        with (
            mock.patch.object(cv, "sd", fake_sd()),
            mock.patch.object(cv, "load_config", return_value=cfg),
            mock.patch.object(cv, "get_pipe", return_value=pipe),
        ):
            return cv.speak_and_highlight(text, provider="kokoro", record_history=False,
                                          render=False, show_stats=True), synthesized

    def test_first_audio_does_not_wait_for_later_sentences(self):
        stats, _ = self.speak("One two. Three four. Five six.", slow_sentence_s=0.3)
        self.assertLess(stats["ttfa"], 0.2)
        self.assertGreater(stats["gen_time"], 0.5)
        self.assertTrue(stats["finished"])
        self.assertEqual(stats["words"], 6)

    def test_stop_ends_playback_and_synthesis(self):
        stats, synthesized = self.speak("A b. C d. E f. G h.", slow_sentence_s=0.2, stop_after=0.1)
        self.assertFalse(stats["finished"])
        time.sleep(0.5)
        self.assertLess(len(synthesized), 4)


if __name__ == "__main__":
    unittest.main()
