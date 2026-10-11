"""Regression coverage for the combined voice contributions."""
import multiprocessing
import os
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

# Spawned session writers need file locking, not PortAudio device discovery.
# Keep this module headless even on hosts with sounddevice installed.
with mock.patch.dict(sys.modules, {"sounddevice": None}):
    import claude_voice as cv


@pytest.mark.parametrize("text,extra,expected", [
    ("--json", {"json": "jason"}, "dash dash jason"),
    ("--pytest", {"pytest": "pie test"}, "dash dash pie test"),
    ("--json=value", {"json": "jason"}, "dash dash jason equals value"),
    ("--json-output", {"json": "jason"}, "dash dash jason output"),
    ("-j", {"j": "jay"}, "dash jay"),
])
def test_pronunciation_inside_flag(text, extra, expected):
    assert cv.fix_pronunciation(text, extra) == expected


def test_streaming_applies_custom_pronunciation():
    spoken = []

    def pipe(sentence, **kwargs):
        spoken.append(sentence)
        yield SimpleNamespace(audio=SimpleNamespace(numpy=lambda: np.ones(100)))

    with mock.patch.object(cv, "get_pipe", return_value=pipe):
        list(cv.kokoro_sentences("pytest.", "af_heart", 1.0,
                                 {"pronunciations": {"pytest": "pie test"}}))
    assert spoken == ["pie test."]


def test_preempted_producer_cannot_overlap_new_synthesis():
    first_entered = threading.Event()
    release_first = threading.Event()
    second_requested = threading.Event()
    second_entered = threading.Event()
    errors = []

    def pieces(provider, text, voice, speed, cfg):
        if text == "first":
            first_entered.set()
            if not release_first.wait(5):
                raise RuntimeError("test producer timed out")
        else:
            second_entered.set()
        return
        yield

    def speak(text):
        if text == "second":
            second_requested.set()
        try:
            cv.speak_and_highlight(text, render=False, record_history=False, show_stats=True)
        except Exception as exc:
            errors.append(exc)

    cfg = dict(cv.default_config(), chime=False)
    with (mock.patch.object(cv, "load_config", return_value=cfg),
          mock.patch.object(cv, "_require_audio"),
          mock.patch.object(cv, "_synth_pieces", pieces)):
        first = threading.Thread(target=speak, args=("first",))
        second = threading.Thread(target=speak, args=("second",))
        try:
            first.start()
            assert first_entered.wait(2)
            cv._player.stop()
            first.join(2)
            assert not first.is_alive(), "stop should stay responsive"
            second.start()
            assert second_requested.wait(2)
            assert not second_entered.wait(0.15)
            release_first.set()
            assert second_entered.wait(2)
        finally:
            release_first.set()
            first.join(5)
            if second.ident is not None:
                second.join(5)
    assert not errors
    assert not second.is_alive()


def _session_writer(runtime, session_id, verb, value, start):
    cv.RUNTIME_DIR = runtime
    cv.SESSIONS_PATH = os.path.join(runtime, "sessions.json")
    start.wait(5)
    cv._update_session(session_id, verb, value)


def test_session_updates_preserve_concurrent_writers(monkeypatch):
    with tempfile.TemporaryDirectory() as runtime:
        monkeypatch.setattr(cv, "RUNTIME_DIR", runtime)
        monkeypatch.setattr(cv, "SESSIONS_PATH", os.path.join(runtime, "sessions.json"))
        ctx = multiprocessing.get_context("spawn")
        start = ctx.Event()
        requests = [(f"session-{i}", "on", None) for i in range(8)]
        requests += [("shared", "voice", "af_heart"), ("shared", "off", None)]
        processes = [ctx.Process(target=_session_writer,
                                args=(runtime, sid, verb, value, start))
                     for sid, verb, value in requests]
        try:
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(10)
                assert process.exitcode == 0
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join()
        sessions = cv._load_sessions()
        assert len(sessions) == 9
        assert sessions["shared"]["voice"] == "af_heart"
        assert sessions["shared"]["enabled"] is False
        assert os.stat(cv.SESSIONS_PATH).st_mode & 0o777 == 0o600
        cv._update_session("shared", "clear")
        assert "shared" not in cv._load_sessions()
