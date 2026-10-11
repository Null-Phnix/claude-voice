"""The CLI should still load when an audio backend is not installed."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


class ImportResilienceTests(unittest.TestCase):
    def test_missing_portaudio_degrades_to_provider_error_instead_of_import_crash(self):
        with tempfile.TemporaryDirectory(prefix="cv-import-test-") as tmp:
            Path(tmp, "sounddevice.py").write_text(
                'raise OSError("PortAudio library not found")\n',
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join((tmp, str(REPO_ROOT)))
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import claude_voice; "
                    "assert claude_voice.sd is None; "
                    "assert claude_voice.np is not None; "
                    "exec('try:\\n claude_voice._require_audio()\\n'"
                    "'except claude_voice.ProviderError:\\n pass\\n'"
                    "'else:\\n raise AssertionError(\"audio should be unavailable\")')",
                ],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
