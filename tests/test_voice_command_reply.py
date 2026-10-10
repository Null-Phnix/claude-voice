"""The reply to a /voice command must not be spoken."""

import json
import os
import tempfile
import unittest

import claude_voice as cv


class VoiceCommandTurnTests(unittest.TestCase):
    def transcript(self, *entries):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        self.addCleanup(os.unlink, f.name)
        for e in entries:
            f.write(json.dumps(e) + "\n")
        f.close()
        return {"transcript_path": f.name}

    def user(self, content, **extra):
        return {"type": "user", "message": {"role": "user", "content": content}, **extra}

    def test_reply_to_voice_command_is_skipped(self):
        data = self.transcript(
            self.user("<command-message>voice</command-message>\n"
                      "<command-name>/voice</command-name>\n<command-args>pause</command-args>"),
            self.user([{"type": "text", "text": "## Voice control result"}], isMeta=True),
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Paused."}]}},
        )
        self.assertTrue(cv._turn_was_voice_command(data))

    def test_normal_turn_with_tool_results_is_spoken(self):
        data = self.transcript(
            self.user("<command-name>/voice</command-name>"),
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
            self.user("fix the bug"),
            self.user([{"type": "tool_result", "content": "done"}]),
        )
        self.assertFalse(cv._turn_was_voice_command(data))

    def test_missing_transcript_is_spoken(self):
        self.assertFalse(cv._turn_was_voice_command({"transcript_path": "/nonexistent"}))


if __name__ == "__main__":
    unittest.main()
