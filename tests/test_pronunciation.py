"""Pronunciation substitutions must preserve displayed-word timing."""

from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

import claude_voice as cv


class PronunciationTests(unittest.TestCase):
    def test_punctuation_terminated_terms_match_at_boundaries(self):
        for text, expected in (
            ("local vs. cloud", "local versus cloud"),
            ("vs.", "versus"),
            ("(vs.)", "(versus)"),
            ("vs.\ncloud", "versus\ncloud"),
        ):
            with self.subTest(text=text):
                self.assertEqual(cv.fix_pronunciation(text), expected)

    def test_terms_do_not_match_inside_longer_identifiers(self):
        for text in ("revs.", "_vs.", "vs.cloud", "vs._private", "APIs"):
            with self.subTest(text=text):
                self.assertEqual(cv.fix_pronunciation(text), text)

    def test_snake_case_parts_are_pronounced_as_words(self):
        self.assertEqual(cv.fix_pronunciation("GPU_0"), "G P U 0")

    def test_existing_word_pronunciations_still_match(self):
        self.assertEqual(cv.fix_pronunciation("API, GPU."), "A P I, G P U.")


class KokoroTimingTests(unittest.TestCase):
    def synthesize(self, text, silent_sentence=None):
        spoken = []

        def pipe(sentence, **kwargs):
            spoken.append(sentence)
            if sentence == silent_sentence:
                return
            # One second per spoken word makes sentence offsets deterministic.
            samples = np.zeros(len(sentence.split()) * cv.SAMPLE_RATE, dtype=np.float32)
            yield SimpleNamespace(audio=SimpleNamespace(numpy=lambda: samples))

        with (
            mock.patch.object(cv, "get_pipe", return_value=pipe),
            mock.patch.object(cv, "np", np),
        ):
            audio, rate, timings = cv.synth_kokoro(text, "test", 1.0, {})
        self.assertEqual(len(timings), len(text.split()))
        return spoken, len(audio) / rate, timings

    def test_period_replacement_preserves_following_sentence_timings(self):
        spoken, duration, timings = self.synthesize(
            "Compare local vs. cloud. Choose wisely."
        )
        self.assertIn("versus", " ".join(spoken))
        self.assertNotIn("vs.", " ".join(spoken))
        self.assertEqual(duration, 6.0)
        self.assertEqual(timings[3][0], 3.0)  # cloud.
        self.assertEqual(timings[4][0], 4.0)  # Choose
        self.assertEqual(timings[-1][1], duration)

    def test_multiword_replacements_keep_original_display_word_count(self):
        _, duration, timings = self.synthesize("API vs. CLI. Done.")
        self.assertEqual(duration, 8.0)
        self.assertEqual(timings[2][0], 4.0)  # CLI.
        self.assertEqual(timings[3][0], 7.0)  # Done.
        self.assertEqual(timings[-1][1], duration)

    def test_silent_sentence_keeps_its_display_words_without_audio_offset(self):
        _, duration, timings = self.synthesize("Empty. Ready now.", "Empty.")
        self.assertEqual(duration, 2.0)
        self.assertEqual(timings[0], (0.0, 0.0))
        self.assertEqual(timings[1][0], 0.0)
        self.assertEqual(timings[-1][1], duration)


if __name__ == "__main__":
    unittest.main()
