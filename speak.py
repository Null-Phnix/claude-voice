#!/usr/bin/env python3
"""
claude-voice: TTS with karaoke-style word highlighting for Claude Code.

The missing half of Claude Code's voice mode. You talk to Claude,
Claude talks back — fully local, zero API keys, one file.

Uses Kokoro TTS (82M params, runs on CPU) with real-time word-by-word
terminal highlighting. Installs as a Claude Code Stop hook.

Commands:
    claude-voice setup              Install hook into Claude Code
    claude-voice demo               Run a polished demo for screen recording
    claude-voice benchmark          Measure and display latency stats
    claude-voice on / off           Toggle voice on or off
    claude-voice --voices           List available voices
    claude-voice --voice af_nova "text"   Speak with a specific voice
"""
import argparse
import json
import os
import re
import select
import signal
import socket
import subprocess
import sys
import termios
import threading
import time
import tty
import warnings

warnings.filterwarnings("ignore")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np
import sounddevice as sd

# ── defaults ──
DEFAULT_VOICE = "af_heart"
SAMPLE_RATE = 24000
WINDOW = 8
MIN_CHARS = 30
MAX_CHARS = 1500
DONE_PAUSE = 0.5
CHIME_ENABLED = True
CONFIG_PATH = os.path.expanduser("~/.config/claude-voice/config.json")
SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
SCRIPT_PATH = os.path.abspath(__file__)

# ── daemon / runtime state ──
RUNTIME_DIR = os.path.expanduser("~/.cache/claude-voice")
SOCK_PATH = os.path.join(RUNTIME_DIR, "daemon.sock")
PID_PATH = os.path.join(RUNTIME_DIR, "daemon.pid")
LOG_PATH = os.path.join(RUNTIME_DIR, "daemon.log")
DAEMON_IDLE_TIMEOUT = 1800            # seconds before idle daemon exits
DAEMON_SPAWN_WAIT = 15                # max seconds to wait for cold-spawned daemon
ACTIVE_TTY_STALE = 600                # claim auto-expires after 10 min

# ── dev pronunciation fixes ──
PRONOUNCE = {
    "CLI": "C L I",
    "API": "A P I",
    "GPU": "G P U",
    "CPU": "C P U",
    "TUI": "T U I",
    "MCP": "M C P",
    "LLM": "L L M",
    "TTS": "T T S",
    "STT": "S T T",
    "SSH": "S S H",
    "SQL": "sequel",
    "YAML": "yaml",
    "JSON": "jason",
    "PyPI": "pie pee eye",
    "npm": "N P M",
    "kwargs": "keyword args",
    "stdout": "standard out",
    "stderr": "standard error",
    "stdin": "standard in",
    "async": "a-sink",
    "sudo": "sue-doo",
    "nginx": "engine-x",
    "kubectl": "kube-control",
    "wget": "w-get",
}

# ── ANSI ──
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
HIGHLIGHT = "\033[1;38;2;120;200;255m"
UNDERLINE = "\033[4m"
NEAR = "\033[38;2;80;150;210m"
SPOKEN = "\033[38;2;65;65;85m"
LABEL = "\033[38;2;90;90;120m"
BAR_FILL = "\033[38;2;120;200;255m"
BAR_EMPTY = "\033[38;2;40;40;55m"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
GREEN = "\033[38;2;100;220;100m"
RED = "\033[38;2;220;80;80m"
CYAN = "\033[38;2;120;200;255m"

_pipe = None
_tty = None
_interrupted = False
_config = None
_tty_fd = None
_old_term = None

# Daemon-only: who currently owns the voice, and when the active claim was set.
_active_tty: str | None = None
_active_tty_t: float = 0.0
_playback_lock = threading.Lock()
_daemon_idle_t0: float = 0.0

VOICE_LIST = {
    "af_heart": "American female, warm & expressive",
    "af_nova": "American female, clear & professional",
    "af_alloy": "American female, smooth & neutral",
    "af_sky": "American female, bright",
    "am_adam": "American male, natural",
    "am_fenrir": "American male, deep & strong",
    "am_michael": "American male, casual",
    "am_onyx": "American male, smooth & confident",
    "bm_george": "British male, polished",
    "bm_daniel": "British male, warm",
    "bf_emma": "British female, clear",
    "bf_isabella": "British female, elegant",
}

DEMO_TEXT = (
    "Done. Both repos pushed to GitHub with clean commit history. "
    "The TUI now supports search by company name, color-coded scores, "
    "and one-key status shortcuts. Eighteen new jobs matched your profile "
    "since the last scan. Three are above the ninety score threshold."
)

BENCHMARK_SENTENCES = [
    "Commit pushed.",
    "The function has been refactored to reduce complexity and improve readability.",
    "I've analyzed the codebase and identified three potential memory leaks in the connection pooling layer. The first is in the retry handler where connections aren't released on timeout. The second is a reference cycle between the cache and the session manager. The third is more subtle.",
]


def _restore_terminal():
    """Restore terminal to normal mode."""
    global _old_term, _tty_fd
    if _old_term is not None and _tty_fd is not None:
        try:
            termios.tcsetattr(_tty_fd, termios.TCSADRAIN, _old_term)
        except (termios.error, OSError):
            pass
        _old_term = None


def _handle_signal(sig, frame):
    global _interrupted
    _interrupted = True
    sd.stop()
    _restore_terminal()
    if _tty:
        _tty.write("\033[1A\r\033[K\033[1A\r\033[K\033[1A\r\033[K")
        _tty.write(SHOW_CURSOR)
        _tty.flush()
    sys.exit(0)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _start_keypress_listener(tty_path: str = "/dev/tty"):
    """Start a background thread that sets _interrupted on any keypress.

    `tty_path` lets the daemon listen on the client's terminal rather than
    its own (the daemon has no controlling tty when detached).
    """
    global _tty_fd, _old_term

    def _listen():
        global _interrupted, _tty_fd, _old_term
        fd = None
        try:
            fd = os.open(tty_path, os.O_RDONLY)
            _tty_fd = fd
            _old_term = termios.tcgetattr(fd)
            tty.setraw(fd)
            while not _interrupted:
                # Use select with timeout so we can check _interrupted
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready:
                    os.read(fd, 1)
                    _interrupted = True
                    sd.stop()
                    break
        except (OSError, termios.error):
            pass
        finally:
            _restore_terminal()
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    t = threading.Thread(target=_listen, daemon=True)
    t.start()
    return t


# ── config ──

def load_config() -> dict:
    global _config
    if _config is not None:
        return _config
    defaults = {
        "voice": DEFAULT_VOICE,
        "min_chars": MIN_CHARS,
        "max_chars": MAX_CHARS,
        "window": WINDOW,
        "chime": CHIME_ENABLED,
        "done_pause": DONE_PAUSE,
        "enabled": True,
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                user = json.load(f)
            defaults.update(user)
        except (json.JSONDecodeError, OSError):
            pass
    _config = defaults
    return _config


def save_config(cfg: dict):
    global _config
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    _config = cfg


def save_default_config():
    if not os.path.exists(CONFIG_PATH):
        save_config({
            "voice": DEFAULT_VOICE,
            "min_chars": MIN_CHARS,
            "max_chars": MAX_CHARS,
            "chime": True,
            "enabled": True,
        })


# ── model ──

def get_pipe():
    global _pipe
    if _pipe is None:
        from kokoro import KPipeline
        _pipe = KPipeline(lang_code='a')
    return _pipe


def get_tty(tty_path: str = "/dev/tty"):
    """Open a writable tty for karaoke rendering.

    `tty_path` defaults to the controlling terminal of the current process.
    The daemon passes the client's tty path here so output lands in the
    user's terminal, not the daemon's (detached) one.
    """
    global _tty
    try:
        _tty = open(tty_path, "w")
    except OSError:
        _tty = sys.stderr
    return _tty


# ── chimes ──

def play_chime_start():
    sr = 44100
    t = np.linspace(0, 0.08, int(sr * 0.08), False)
    freq = np.linspace(600, 900, len(t))
    tone = np.sin(2 * np.pi * freq * t) * 0.15
    fade = np.minimum(t / 0.02, 1.0) * np.minimum((0.08 - t) / 0.02, 1.0)
    tone *= fade
    sd.play(tone.astype(np.float32), samplerate=sr)
    sd.wait()


def play_chime_end():
    sr = 44100
    t = np.linspace(0, 0.08, int(sr * 0.08), False)
    freq = np.linspace(900, 600, len(t))
    tone = np.sin(2 * np.pi * freq * t) * 0.12
    fade = np.minimum(t / 0.02, 1.0) * np.minimum((0.08 - t) / 0.02, 1.0)
    tone *= fade
    sd.play(tone.astype(np.float32), samplerate=sr)
    sd.wait()


# ── text processing ──

def is_mostly_code(text: str) -> bool:
    """Return True if the response is mostly code blocks — skip TTS."""
    code_blocks = re.findall(r'```[\s\S]*?```', text)
    code_chars = sum(len(b) for b in code_blocks)
    return len(text) > 0 and (code_chars / len(text)) > 0.5


def clean_for_speech(text: str) -> str:
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'[*_#>|]', '', text)
    text = re.sub(r'\|[^\n]+\|', '', text)  # strip markdown tables
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)  # numbered lists
    text = re.sub(r'^\s*[-•]\s+', '', text, flags=re.MULTILINE)  # bullet points
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'-{2,}', ' ', text)
    return text.strip()


def fix_pronunciation(text: str) -> str:
    for term, replacement in PRONOUNCE.items():
        text = re.sub(rf'\b{re.escape(term)}\b', replacement, text)
    return text


def split_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


# ── timing ──

def estimate_word_timings(words: list[str], duration: float) -> list[tuple[float, float]]:
    total_chars = sum(len(w) for w in words)
    if total_chars == 0:
        return [(0.0, duration)] * len(words)
    timings = []
    cursor = 0.0
    for w in words:
        word_dur = (len(w) / total_chars) * duration
        timings.append((cursor, cursor + word_dur))
        cursor += word_dur
    return timings


# ── rendering ──

def render_karaoke(all_words: list[str], idx: int, window: int) -> str:
    total = len(all_words)
    start = max(0, idx - window)
    end = min(total, idx + window + 1)

    parts = []
    if start > 0:
        parts.append(f"{DIM}...{RESET}")

    for i in range(start, end):
        if i < idx - 1:
            parts.append(f"{SPOKEN}{all_words[i]}{RESET}")
        elif i == idx - 1 or i == idx + 1:
            parts.append(f"{NEAR}{all_words[i]}{RESET}")
        elif i == idx:
            parts.append(f"{HIGHLIGHT}{UNDERLINE}{all_words[i]}{RESET}")
        else:
            parts.append(f"{DIM}{all_words[i]}{RESET}")

    if end < total:
        parts.append(f"{DIM}...{RESET}")

    return " ".join(parts)


def mini_bar(current: int, total: int, width: int = 20) -> str:
    if total == 0:
        return ""
    filled = int((current / total) * width)
    return f"{BAR_FILL}{'━' * filled}{BAR_EMPTY}{'━' * (width - filled)}{RESET} {LABEL}{current}/{total}{RESET}"


# ── core TTS + highlight loop ──

def generate_audio(text: str, voice: str) -> tuple[list, float]:
    """Generate audio for text. Returns (sentence_audio_list, generation_time)."""
    pipe = get_pipe()
    speech_text = fix_pronunciation(text)
    sentences = split_sentences(speech_text)

    t0 = time.monotonic()
    sentence_audio = []
    for sentence in sentences:
        chunks = []
        for result in pipe(sentence, voice=voice):
            chunks.append(result.audio.numpy())
        if chunks:
            sentence_audio.append(np.concatenate(chunks))
        else:
            sentence_audio.append(None)
    gen_time = time.monotonic() - t0

    return sentence_audio, gen_time


def speak_and_highlight(text: str, voice: str, show_stats: bool = False,
                        tty_path: str = "/dev/tty") -> dict:
    cfg = load_config()
    window = cfg.get("window", WINDOW)
    done_pause = cfg.get("done_pause", DONE_PAUSE)
    chime = cfg.get("chime", CHIME_ENABLED)
    global _interrupted
    _interrupted = False

    t0 = time.monotonic()

    display_sentences = split_sentences(text)
    all_words = text.split()
    total_words = len(all_words)

    # Generate audio
    sentence_audio, gen_time = generate_audio(text, voice)

    # Concatenate into one seamless buffer
    all_audio_parts = []
    word_boundaries = []
    sample_offset = 0

    # Use display sentences for word boundaries, fall back gracefully
    for i, audio in enumerate(sentence_audio):
        if i < len(display_sentences):
            words = display_sentences[i].split()
        else:
            words = []
        if audio is not None:
            word_boundaries.append((words, sample_offset, sample_offset + len(audio)))
            all_audio_parts.append(audio)
            sample_offset += len(audio)
        else:
            word_boundaries.append((words, sample_offset, sample_offset))

    if not all_audio_parts:
        return {}

    full_audio = np.concatenate(all_audio_parts)
    audio_duration = len(full_audio) / SAMPLE_RATE

    # Build global word timings
    global_timings = []
    for words, start_sample, end_sample in word_boundaries:
        seg_duration = (end_sample - start_sample) / SAMPLE_RATE
        seg_start = start_sample / SAMPLE_RATE
        word_timings = estimate_word_timings(words, seg_duration)
        for wstart, wend in word_timings:
            global_timings.append((seg_start + wstart, seg_start + wend))

    tty = get_tty(tty_path)

    if chime:
        play_chime_start()

    # Start keypress listener — any key interrupts playback
    listener = _start_keypress_listener(tty_path)

    tty.write(HIDE_CURSOR)
    header = f"  {LABEL}now speaking{RESET}  {LABEL}|{RESET}  {LABEL}{voice}{RESET}  {DIM}(press any key to skip){RESET}"
    tty.write(f"{header}\n\n")
    tty.flush()

    ttfa = time.monotonic() - t0

    # Play
    playback_start = time.monotonic()
    sd.play(full_audio, samplerate=SAMPLE_RATE)

    for word_idx, (start, end) in enumerate(global_timings):
        if _interrupted:
            break
        elapsed = time.monotonic() - playback_start
        if elapsed < start:
            time.sleep(start - elapsed)
        if _interrupted:
            break

        karaoke = render_karaoke(all_words, word_idx, window)
        bar = mini_bar(word_idx + 1, total_words)
        tty.write(f"\033[1A\r\033[K  {karaoke}\n\r\033[K  {bar}")
        tty.flush()

    sd.stop()
    _restore_terminal()

    total_time = time.monotonic() - t0

    if not _interrupted:
        # Done state — only show if not skipped
        bar = mini_bar(total_words, total_words)
        tty.write(f"\033[1A\r\033[K  {SPOKEN}done{RESET}\n\r\033[K  {bar}")
        tty.flush()
        time.sleep(done_pause)

        if chime:
            play_chime_end()

    # Clear
    tty.write("\033[1A\r\033[K\033[1A\r\033[K\033[1A\r\033[K")
    tty.write(SHOW_CURSOR)
    tty.flush()

    stats = {
        "ttfa": ttfa,
        "gen_time": gen_time,
        "audio_duration": audio_duration,
        "total_time": total_time,
        "words": total_words,
        "chars": len(text),
        "voice": voice,
    }

    # Log to stderr (for hook mode)
    if not show_stats:
        sys.stderr.write(
            f"claude-voice: ttfa={ttfa:.2f}s gen={gen_time:.2f}s "
            f"total={total_time:.2f}s words={total_words} voice={voice}\n"
        )

    if tty is not sys.stderr:
        tty.close()

    return stats


# ── daemon ──
#
# Why a daemon exists: loading Kokoro takes ~6–10s, and the Claude Code Stop
# hook fires a fresh Python process for every assistant response. Without a
# daemon, every response repays that cold-start. The daemon loads the model
# once and accepts requests over a Unix socket. Cold TTFA stays ~6–10s on the
# very first response, but warm TTFA drops to ~0.6s.
#
# It also routes audio per-terminal. When multiple Claude Code sessions
# finish at the same time, only the terminal that "claims" the voice speaks.
# The UserPromptSubmit hook auto-claims when you type a prompt, so the
# natural flow ("typed here → hear here") works without intervention.


def _daemon_log(msg: str) -> None:
    try:
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def _resolve_tty() -> str:
    """Best-effort: a stable path to the user's controlling terminal.

    Hooks are launched with stdin/stdout/stderr as pipes, so `ttyname(fd)`
    on those fds raises. We open /dev/tty (which always refers to the
    *calling* process's controlling terminal) and read its real path —
    that gives a stable identifier like `/dev/ttys001` we can use to
    distinguish terminals.
    """
    for fd in (2, 1, 0):
        try:
            return os.ttyname(fd)
        except OSError:
            continue
    fd = None
    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
        return os.ttyname(fd)
    except OSError:
        return "/dev/tty"
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _daemon_alive() -> bool:
    """Return True if a daemon is reachable on the socket."""
    if not os.path.exists(SOCK_PATH):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(SOCK_PATH)
        s.sendall(json.dumps({"op": "ping"}).encode() + b"\n")
        resp = s.recv(256)
        s.close()
        return b'"ok"' in resp
    except (OSError, socket.timeout):
        return False


def _spawn_daemon() -> None:
    """Spawn the daemon process detached. Returns immediately."""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    log = open(LOG_PATH, "a")
    subprocess.Popen(
        [sys.executable, SCRIPT_PATH, "--daemon"],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
        close_fds=True,
    )


def _send_to_daemon(payload: dict, timeout: float = 2.0) -> dict | None:
    """Send a request to the daemon. Returns the parsed response or None."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(SOCK_PATH)
        s.sendall(json.dumps(payload).encode() + b"\n")
        buf = b""
        while b"\n" not in buf and len(buf) < 8192:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode("utf-8", errors="replace"))
    except (OSError, socket.timeout, json.JSONDecodeError):
        return None


def _ensure_daemon(wait_seconds: int = DAEMON_SPAWN_WAIT) -> bool:
    """Spawn the daemon if not running and wait up to `wait_seconds` for ready.

    Returns True if a live daemon is reachable when the function returns.
    """
    if _daemon_alive():
        return True
    _spawn_daemon()
    for _ in range(wait_seconds * 5):
        time.sleep(0.2)
        if _daemon_alive():
            return True
    return False


def _handle_client(conn: socket.socket) -> None:
    """One connection's lifecycle. Runs on a daemon worker thread."""
    global _daemon_idle_t0, _active_tty, _active_tty_t, _interrupted
    try:
        conn.settimeout(2.0)
        buf = b""
        while b"\n" not in buf and len(buf) < 2_000_000:
            chunk = conn.recv(8192)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        if not line:
            return
        req = json.loads(line.decode("utf-8", errors="replace"))
        _daemon_idle_t0 = time.monotonic()

        op = req.get("op", "speak")

        if op == "ping":
            conn.sendall(json.dumps({"ok": True}).encode() + b"\n")
            return

        if op == "shutdown":
            conn.sendall(json.dumps({"ok": True}).encode() + b"\n")
            _daemon_log("shutdown requested")
            os.kill(os.getpid(), signal.SIGTERM)
            return

        if op == "claim":
            _active_tty = req.get("tty_path") or _active_tty
            _active_tty_t = time.monotonic()
            _daemon_log(f"claim: {_active_tty}")
            conn.sendall(json.dumps({"ok": True, "active_tty": _active_tty}).encode() + b"\n")
            return

        if op == "unclaim":
            _daemon_log(f"unclaim (was {_active_tty})")
            _active_tty = None
            _active_tty_t = 0.0
            conn.sendall(json.dumps({"ok": True}).encode() + b"\n")
            return

        if op == "status":
            stale = _active_tty and (time.monotonic() - _active_tty_t > ACTIVE_TTY_STALE)
            conn.sendall(json.dumps({
                "ok": True,
                "pid": os.getpid(),
                "active_tty": None if stale else _active_tty,
                "active_tty_age_s": (time.monotonic() - _active_tty_t) if _active_tty else None,
                "idle_s": time.monotonic() - _daemon_idle_t0,
            }).encode() + b"\n")
            return

        # op == "speak"
        text = req.get("text", "")
        voice = req.get("voice") or load_config().get("voice", DEFAULT_VOICE)
        tty_path = req.get("tty_path") or "/dev/tty"
        is_long = bool(req.get("long"))

        if not text.strip():
            conn.sendall(json.dumps({"error": "empty"}).encode() + b"\n")
            return

        # Multi-terminal gating: if a fresh claim exists and points elsewhere,
        # silently skip so two simultaneous responses don't overlap.
        stale = _active_tty and (time.monotonic() - _active_tty_t > ACTIVE_TTY_STALE)
        if _active_tty and not stale and tty_path != _active_tty:
            _daemon_log(f"skip: tty {tty_path} != active {_active_tty}")
            conn.sendall(json.dumps({"skipped": "not_active_tty"}).encode() + b"\n")
            return

        # Ack the request immediately, then play synchronously under a lock so
        # a second speak request preempts (not overlaps) the first.
        conn.sendall(json.dumps({"queued": True}).encode() + b"\n")
        try:
            conn.close()
        except OSError:
            pass

        # Preempt current playback if any
        _interrupted = True
        sd.stop()

        with _playback_lock:
            _interrupted = False
            try:
                speak_and_highlight(text, voice, show_stats=False, tty_path=tty_path)
            except Exception as e:
                _daemon_log(f"playback error: {e}")
    except (json.JSONDecodeError, OSError, ValueError) as e:
        try:
            conn.sendall(json.dumps({"error": str(e)}).encode() + b"\n")
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def cmd_daemon() -> None:
    """Run as TTS daemon. Loads Kokoro once, serves the Unix socket."""
    global _daemon_idle_t0

    os.makedirs(RUNTIME_DIR, exist_ok=True)
    if _daemon_alive():
        _daemon_log("daemon already running, exiting")
        return

    # Stale socket cleanup — a prior daemon that crashed without unlinking.
    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass

    try:
        with open(PID_PATH, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    _daemon_log("loading kokoro model...")
    t0 = time.monotonic()
    get_pipe()
    _daemon_log(f"kokoro loaded in {time.monotonic()-t0:.2f}s")

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chmod(SOCK_PATH, 0o600)
    srv.listen(8)
    srv.settimeout(5.0)
    _daemon_log(f"listening on {SOCK_PATH}")

    _daemon_idle_t0 = time.monotonic()

    def _cleanup(*_args):
        for path in (SOCK_PATH, PID_PATH):
            try:
                os.unlink(path)
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    while True:
        if time.monotonic() - _daemon_idle_t0 > DAEMON_IDLE_TIMEOUT:
            _daemon_log("idle timeout, exiting")
            _cleanup()

        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError as e:
            _daemon_log(f"accept error: {e}")
            continue

        threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()


# ── commands ──

def cmd_setup():
    """Install claude-voice hook into Claude Code settings."""
    save_default_config()

    # Read existing settings
    settings = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    hooks = settings.get("hooks", {})

    def _has_us(event: str) -> bool:
        for entry in hooks.get(event, []):
            for h in entry.get("hooks", []):
                if "claude-voice" in str(h) or "speak.py" in str(h) or "claude_voice" in str(h):
                    return True
        return False

    cmd = f"python3 {SCRIPT_PATH}"
    added = []

    if not _has_us("Stop"):
        hooks.setdefault("Stop", []).append({
            "matcher": "",
            "hooks": [{"type": "command", "command": cmd, "timeout": 60, "async": True}],
        })
        added.append("Stop")

    # UserPromptSubmit auto-claims the current terminal so a Stop hook in a
    # different tab does not also speak when responses finish simultaneously.
    if not _has_us("UserPromptSubmit"):
        hooks.setdefault("UserPromptSubmit", []).append({
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{cmd} claim", "timeout": 5, "async": True}],
        })
        added.append("UserPromptSubmit (claim)")

    if not added:
        print(f"{GREEN}claude-voice is already installed.{RESET}")
        print(f"Config: {CONFIG_PATH}")
        return

    settings["hooks"] = hooks
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)

    print(f"{GREEN}claude-voice installed.{RESET}")
    print(f"  Hooks added:   {', '.join(added)}")
    print(f"  Settings file: {SETTINGS_PATH}")
    print(f"  Config at:     {CONFIG_PATH}")
    print(f"  Voice:         {load_config().get('voice', DEFAULT_VOICE)}")
    print(f"\nRestart Claude Code for the hooks to take effect.")
    print(f"Run {CYAN}claude-voice demo{RESET} to test it now.")


def cmd_demo():
    """Run a polished demo — perfect for screen recording."""
    cfg = load_config()
    voice = cfg.get("voice", DEFAULT_VOICE)

    print(f"\n  {BOLD}claude-voice demo{RESET}")
    print(f"  {LABEL}voice: {voice}  |  engine: kokoro 82M  |  local{RESET}\n")
    time.sleep(0.5)

    speak_and_highlight(DEMO_TEXT, voice, show_stats=True)

    print(f"\n  {GREEN}Demo complete.{RESET}")
    print(f"  {LABEL}That ran fully local — no API keys, no cloud, no internet.{RESET}\n")


def cmd_benchmark():
    """Run latency benchmarks and print shareable stats."""
    cfg = load_config()
    voice = cfg.get("voice", DEFAULT_VOICE)

    print(f"\n  {BOLD}claude-voice benchmark{RESET}")
    print(f"  {LABEL}voice: {voice}  |  engine: kokoro 82M{RESET}")
    print(f"  {LABEL}running 3 tests...{RESET}\n")

    labels = ["Short (2 words)", "Medium (15 words)", "Long (50 words)"]
    results = []

    for i, sentence in enumerate(BENCHMARK_SENTENCES):
        print(f"  {DIM}[{i+1}/3] {labels[i]}...{RESET}", end="", flush=True)
        stats = speak_and_highlight(sentence, voice, show_stats=True)
        results.append(stats)
        print(f"\r\033[K  {GREEN}[{i+1}/3] {labels[i]}{RESET}  "
              f"ttfa={stats['ttfa']:.2f}s  gen={stats['gen_time']:.2f}s  "
              f"audio={stats['audio_duration']:.1f}s  total={stats['total_time']:.2f}s")
        time.sleep(0.3)

    # Summary
    avg_ttfa = sum(r["ttfa"] for r in results) / len(results)
    avg_gen = sum(r["gen_time"] for r in results) / len(results)
    total_words = sum(r["words"] for r in results)
    total_chars = sum(r["chars"] for r in results)

    print(f"\n  {'─' * 52}")
    print(f"  {BOLD}Results{RESET}")
    print(f"  {LABEL}Avg time to first audio:{RESET}  {CYAN}{avg_ttfa:.2f}s{RESET}")
    print(f"  {LABEL}Avg generation time:{RESET}      {CYAN}{avg_gen:.2f}s{RESET}")
    print(f"  {LABEL}Total words spoken:{RESET}        {total_words}")
    print(f"  {LABEL}Total chars processed:{RESET}     {total_chars}")
    print(f"  {LABEL}Voice:{RESET}                     {voice}")
    print(f"  {LABEL}Engine:{RESET}                    Kokoro 82M (local)")
    print(f"  {'─' * 52}")

    # Shareable one-liner
    print(f"\n  {DIM}Shareable:{RESET}")
    print(f"  claude-voice benchmark: ttfa={avg_ttfa:.2f}s avg_gen={avg_gen:.2f}s "
          f"voice={voice} engine=kokoro-82M local=true")
    print()


def cmd_toggle(enable: bool):
    """Toggle claude-voice on or off."""
    cfg = load_config()
    cfg["enabled"] = enable
    save_config(cfg)
    state = f"{GREEN}on{RESET}" if enable else f"{RED}off{RESET}"
    print(f"  claude-voice is now {state}")


def cmd_claim() -> None:
    """Mark the current terminal as the voice owner.

    Fire-and-forget: if no daemon is running, do nothing rather than blocking
    the UserPromptSubmit hook for a cold spawn. The next speak request will
    spawn the daemon, and the following prompt will re-claim.
    """
    if not _daemon_alive():
        return
    _send_to_daemon({"op": "claim", "tty_path": _resolve_tty()}, timeout=1.0)


def cmd_unclaim() -> None:
    if not _daemon_alive():
        return
    _send_to_daemon({"op": "unclaim"}, timeout=1.0)


def cmd_daemon_status() -> None:
    if not _daemon_alive():
        print(f"  {RED}daemon not running{RESET}")
        return
    resp = _send_to_daemon({"op": "status"}, timeout=1.0) or {}
    pid = resp.get("pid", "?")
    active = resp.get("active_tty") or f"{DIM}(none){RESET}"
    idle = resp.get("idle_s")
    idle_str = f"{idle:.0f}s" if isinstance(idle, (int, float)) else "?"
    print(f"  {GREEN}daemon running{RESET}  pid={pid}  idle={idle_str}  active_tty={active}")


def cmd_daemon_stop() -> None:
    if not _daemon_alive():
        print(f"  {DIM}daemon not running{RESET}")
        return
    _send_to_daemon({"op": "shutdown"}, timeout=1.0)
    print(f"  daemon stopped")


def main():
    SUBCOMMANDS = {
        "setup", "demo", "benchmark", "on", "off",
        "claim", "unclaim", "daemon-status", "daemon-stop",
    }
    if len(sys.argv) >= 2 and sys.argv[1] in SUBCOMMANDS:
        cmd = sys.argv[1]
        if cmd == "setup":          cmd_setup()
        elif cmd == "demo":         cmd_demo()
        elif cmd == "benchmark":    cmd_benchmark()
        elif cmd == "on":           cmd_toggle(True)
        elif cmd == "off":          cmd_toggle(False)
        elif cmd == "claim":        cmd_claim()
        elif cmd == "unclaim":      cmd_unclaim()
        elif cmd == "daemon-status": cmd_daemon_status()
        elif cmd == "daemon-stop":  cmd_daemon_stop()
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="Claude Code TTS with word highlighting",
        usage="claude-voice [setup|demo|benchmark|on|off|claim|unclaim|daemon-status|daemon-stop] or claude-voice [options] [text]",
    )
    parser.add_argument("text", nargs="*", help="Text to speak")
    parser.add_argument("--voice", "-v", default=None, help="Kokoro voice ID")
    parser.add_argument("--voices", action="store_true", help="List available voices")
    parser.add_argument("--long", action="store_true", help="No truncation — speak full text")
    parser.add_argument("--daemon", action="store_true", help="Run as TTS daemon (internal)")
    parser.add_argument("--no-daemon", action="store_true", help="Force in-process speak; never spawn or use the daemon")
    args = parser.parse_args()

    if args.daemon:
        cmd_daemon()
        sys.exit(0)

    if args.voices:
        cfg = load_config()
        current = cfg.get("voice", DEFAULT_VOICE)
        print(f"\n  {BOLD}Available voices{RESET}\n")
        for vid, desc in VOICE_LIST.items():
            marker = f" {GREEN}*{RESET}" if vid == current else ""
            print(f"  {CYAN}{vid:16s}{RESET} {desc}{marker}")
        print(f"\n  {DIM}Set default: edit {CONFIG_PATH}{RESET}\n")
        sys.exit(0)

    cfg = load_config()

    if not cfg.get("enabled", True):
        sys.exit(0)

    voice = args.voice or cfg.get("voice", DEFAULT_VOICE)

    text = None
    if args.text:
        text = " ".join(args.text)
    elif not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        # Check for code-heavy responses before parsing
        try:
            data = json.loads(raw)
            raw_msg = data.get("last_assistant_message", "")
            if is_mostly_code(raw_msg):
                sys.exit(0)
            text = raw_msg
        except (json.JSONDecodeError, TypeError):
            text = raw

    if not text or not text.strip():
        sys.exit(0)

    text = clean_for_speech(text)
    if not text:
        sys.exit(0)

    min_chars = cfg.get("min_chars", MIN_CHARS)
    max_chars = cfg.get("max_chars", MAX_CHARS)

    if len(text) < min_chars:
        sys.exit(0)

    if not args.long and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."

    tty_path = _resolve_tty()
    use_daemon = cfg.get("use_daemon", True) and not args.no_daemon

    if use_daemon:
        # If a daemon is alive, send and return — fastest path (~50ms client time).
        # If not, spawn one and wait up to DAEMON_SPAWN_WAIT for it to warm.
        # Falls through to in-process only if the daemon never becomes reachable.
        if _ensure_daemon():
            resp = _send_to_daemon({
                "op": "speak",
                "text": text,
                "voice": voice,
                "tty_path": tty_path,
                "long": args.long,
            }, timeout=5.0)
            if resp is not None and ("queued" in resp or "skipped" in resp):
                sys.exit(0)
            # Daemon reachable but request errored — fall through to in-process

    try:
        speak_and_highlight(text, voice, tty_path=tty_path)
    except Exception:
        if _tty:
            _tty.write(SHOW_CURSOR)
            _tty.flush()
        sys.exit(0)


if __name__ == "__main__":
    main()
