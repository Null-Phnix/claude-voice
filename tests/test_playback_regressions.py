"""Initial controls and failures after the first streamed audio chunk."""

import threading
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

import claude_voice as cv


class RecordingStream:
    """Record the device format without opening a real audio device."""

    instances = []

    def __init__(self, samplerate, channels, dtype, callback):
        self.samplerate = samplerate
        self.closed = threading.Event()
        RecordingStream.instances.append(self)

    def start(self):
        pass

    def stop(self):
        pass

    abort = stop

    def close(self):
        self.closed.set()


@pytest.fixture
def audio(monkeypatch):
    RecordingStream.instances = []
    monkeypatch.setattr(cv, "sd", SimpleNamespace(OutputStream=RecordingStream))
    monkeypatch.setattr(cv, "_player", None)
    monkeypatch.setattr(cv, "_interrupted", False)
    yield RecordingStream.instances
    if cv._player is not None:
        cv._player.stop()
    for stream in RecordingStream.instances:
        assert stream.closed.wait(2), "output stream did not close"


@pytest.mark.parametrize("rate", [22050, 44100])
@pytest.mark.parametrize("control", ["resume", "seek"])
def test_controls_before_first_audio_wait_for_actual_rate(audio, rate, control):
    player = cv.Player()
    try:
        assert player.pause()
        if control == "resume":
            player.resume()
        else:
            assert player.seek(1.0) == 0.0
        assert not audio, "the first chunk must establish the output sample rate"
        player.append(np.ones(rate, dtype=np.float32), rate)
        assert len(audio) == 1
        assert audio[0].samplerate == player.rate == rate
    finally:
        player.stop()


@pytest.mark.parametrize("control", ["resume", "seek"])
def test_controls_on_completed_empty_audio_do_not_open_stream(audio, control):
    player = cv.Player()
    try:
        player.finish_input()
        if control == "resume":
            player.resume()
        else:
            player.seek(0.0)
        assert not audio
        assert player.drained.is_set()
    finally:
        player.stop()


def test_first_chunk_without_early_controls_keeps_its_rate(audio):
    player = cv.Player()
    try:
        player.append(np.ones(22050, dtype=np.float32), 22050)
        assert audio[0].samplerate == player.rate == 22050
    finally:
        player.stop()


@pytest.mark.parametrize("error_type", [cv.ProviderError, RuntimeError])
def test_midstream_failure_reports_cause_and_partial_result(audio, monkeypatch,
                                                          capsys, error_type):
    cfg = dict(cv.default_config(), chime=False, history_enabled=False)
    monkeypatch.setattr(cv, "load_config", lambda: cfg)
    failure = "second-sentence synthesis failure"

    def pieces(*args, **kwargs):
        yield np.ones(2400, dtype=np.float32), 24000, ["First"], [(0.0, 0.1)]
        raise error_type(failure)

    monkeypatch.setattr(cv, "_synth_pieces", pieces)
    result = cv.speak_and_highlight("First. Second.", provider="kokoro",
                                    render=False, record_history=False)
    stderr = capsys.readouterr().err
    assert result["finished"] is False
    assert result["audio_duration"] == 0.1
    assert result["error"] == failure
    assert failure in stderr
    assert "failed" in stderr


def test_device_failure_before_first_audio_still_reaches_daemon_retry(audio,
                                                                    monkeypatch,
                                                                    capsys):
    monkeypatch.setattr(cv, "load_config", lambda: dict(cv.default_config(), chime=False))

    def pieces(*args, **kwargs):
        raise RuntimeError("device unavailable")
        yield

    monkeypatch.setattr(cv, "_synth_pieces", pieces)
    with pytest.raises(RuntimeError, match="device unavailable"):
        cv.speak_and_highlight("Hello.", render=False, record_history=False)
    assert "device unavailable" in capsys.readouterr().err


def test_daemon_does_not_replay_partial_audio_after_failure(audio, monkeypatch, capsys):
    cfg = dict(cv.default_config(), chime=False, history_enabled=False)
    monkeypatch.setattr(cv, "load_config", lambda: cfg)
    monkeypatch.setattr(cv, "reload_config", lambda: cfg)
    monkeypatch.setattr(cv, "_daemon_log", lambda message: None)
    monkeypatch.setattr(cv, "_active_tty", None)
    monkeypatch.setattr(cv, "_speaking_tty", None)
    monkeypatch.setattr(cv, "_muted_ttys", set())
    calls = []

    def pieces(*args, **kwargs):
        calls.append(args[1])
        yield np.ones(2400, dtype=np.float32), 24000, ["First"], [(0.0, 0.1)]
        raise RuntimeError("late device failure")

    monkeypatch.setattr(cv, "_synth_pieces", pieces)
    conn = mock.Mock()
    conn.recv.return_value = b'{"op":"speak","text":"First. Second.","render":false}\n'
    cv._handle_client(conn)
    assert calls == ["First. Second."]
    assert "late device failure" in capsys.readouterr().err


@pytest.mark.parametrize("error", ["synthesis failed", ""])
def test_direct_cli_failure_exits_nonzero(monkeypatch, error):
    monkeypatch.setattr(cv, "load_config", cv.default_config)
    monkeypatch.setattr(cv.sys, "argv", ["claude-voice", "--no-daemon", "Hello."])
    monkeypatch.setattr(cv, "_resolve_tty", lambda: "/dev/null")
    monkeypatch.setattr(cv.os, "isatty", lambda fd: False)
    monkeypatch.setattr(cv, "speak_and_highlight",
                        lambda *args, **kwargs: {"finished": False, "error": error})
    with pytest.raises(SystemExit) as result:
        cv.main()
    assert result.value.code == 1


@pytest.mark.parametrize("command", [cv.cmd_demo, cv.cmd_benchmark])
@pytest.mark.parametrize("failed", [True, False])
def test_demo_and_benchmark_do_not_summarize_incomplete_playback(monkeypatch, capsys,
                                                               command, failed):
    result = {"finished": False, "ttfa": 0.1, "gen_time": 0.2, "audio_duration": 0.1,
              "total_time": 0.2, "words": 1}
    if failed:
        result["error"] = "synthesis failed"
    monkeypatch.setattr(cv, "load_config", cv.default_config)
    speak = mock.Mock(return_value=result)
    monkeypatch.setattr(cv, "speak_and_highlight", speak)
    if failed:
        with pytest.raises(SystemExit) as status:
            command()
        assert status.value.code == 1
    else:
        command()
    stdout = capsys.readouterr().out
    assert "Demo complete" not in stdout
    assert "Avg time to first audio" not in stdout
    assert ("failed" if failed else "stopped") in stdout
    speak.assert_called_once()
