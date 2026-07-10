#!/usr/bin/env python3
"""
claude-voice: TTS with karaoke-style word highlighting for Claude Code.

The other half of Claude Code's voice mode. You talk to Claude,
Claude talks back — local by default, cloud voices when you want them.

Providers:
    kokoro       Local, free, no API key (Kokoro 82M, runs on CPU)
    system       Your OS voice (macOS `say` / espeak) — zero install
    openai       OpenAI TTS (gpt-4o-mini-tts / tts-1)
    elevenlabs   ElevenLabs — with true word-level karaoke timestamps
    grok         xAI Grok TTS (api.x.ai)
    custom       Any OpenAI-compatible /audio/speech endpoint

Commands:
    claude-voice setup              Install Stop hook + /voice slash command
    claude-voice on | off | toggle  Enable / disable speech
    claude-voice status             Show current state
    claude-voice provider <name>    Switch TTS provider
    claude-voice voice <name>       Set voice for current provider
    claude-voice voices [provider]  List voices
    claude-voice key <provider> <k> Store an API key
    claude-voice speed <x>          Playback speed (e.g. 1.2)
    claude-voice volume <x>         Volume 0.0 - 1.0
    claude-voice theme <name>       UI theme (aurora/ember/violet/mint/mono)
    claude-voice demo               Run a polished demo
    claude-voice benchmark          Measure latency stats
    claude-voice doctor             Diagnose install problems
    claude-voice uninstall          Remove hook + slash command
    claude-voice "some text"        Speak arbitrary text
"""
import argparse
import base64
import io
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
import urllib.error
import urllib.request
import warnings
import wave

warnings.filterwarnings("ignore")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

try:
    import numpy as np
    import sounddevice as sd
except ImportError:
    np = None
    sd = None

VERSION = "0.2.0"

# ── defaults ──
SAMPLE_RATE = 24000
WINDOW = 8
MIN_CHARS = 30
MAX_CHARS = 1500
DONE_PAUSE = 0.5
CONFIG_PATH = os.path.expanduser("~/.config/claude-voice/config.json")
ELEVEN_CACHE_PATH = os.path.expanduser("~/.config/claude-voice/elevenlabs_voices.json")
SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
COMMAND_PATH = os.path.expanduser("~/.claude/commands/voice.md")
SCRIPT_PATH = os.path.abspath(__file__)

# ── dev pronunciation fixes (local engines only — cloud models handle these) ──
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

# ── themes ──
THEMES = {
    "aurora": {"accent": (120, 200, 255), "near": (80, 150, 210), "spoken": (65, 65, 85),
               "label": (110, 110, 140), "bar_empty": (40, 40, 55)},
    "ember":  {"accent": (255, 170, 90), "near": (205, 120, 60), "spoken": (85, 65, 55),
               "label": (140, 115, 95), "bar_empty": (55, 42, 35)},
    "violet": {"accent": (195, 145, 255), "near": (140, 100, 205), "spoken": (75, 65, 95),
               "label": (125, 110, 150), "bar_empty": (45, 38, 60)},
    "mint":   {"accent": (110, 235, 185), "near": (70, 175, 140), "spoken": (60, 85, 75),
               "label": (100, 140, 125), "bar_empty": (35, 52, 45)},
    "mono":   {"accent": (235, 235, 235), "near": (170, 170, 170), "spoken": (85, 85, 85),
               "label": (130, 130, 130), "bar_empty": (55, 55, 55)},
}

PROVIDER_COLORS = {
    "kokoro":     (195, 145, 255),
    "system":     (160, 160, 175),
    "openai":     (110, 230, 190),
    "elevenlabs": (255, 150, 80),
    "grok":       (240, 100, 100),
    "custom":     (120, 200, 255),
}

# ── ANSI (theme-dependent globals set by set_theme) ──
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
GREEN = "\033[38;2;100;220;100m"
RED = "\033[38;2;220;80;80m"
YELLOW = "\033[38;2;230;200;90m"


def _rgb(c):
    return f"\033[38;2;{c[0]};{c[1]};{c[2]}m"


HIGHLIGHT = NEAR = SPOKEN = LABEL = BAR_FILL = BAR_EMPTY = ACCENT = CYAN = ""


def set_theme(name: str):
    global HIGHLIGHT, NEAR, SPOKEN, LABEL, BAR_FILL, BAR_EMPTY, ACCENT, CYAN
    t = THEMES.get(name, THEMES["aurora"])
    ACCENT = _rgb(t["accent"])
    HIGHLIGHT = "\033[1m" + ACCENT
    NEAR = _rgb(t["near"])
    SPOKEN = _rgb(t["spoken"])
    LABEL = _rgb(t["label"])
    BAR_FILL = ACCENT
    BAR_EMPTY = _rgb(t["bar_empty"])
    CYAN = ACCENT


set_theme("aurora")

# ── voice catalogs ──
KOKORO_VOICES = {
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

OPENAI_VOICES = {
    "marin": "female, natural & warm (newest)",
    "cedar": "male, natural & grounded (newest)",
    "nova": "female, bright & friendly",
    "shimmer": "female, soft",
    "coral": "female, upbeat",
    "sage": "female, calm",
    "alloy": "neutral, balanced",
    "ash": "male, warm",
    "ballad": "male, expressive",
    "echo": "male, steady",
    "fable": "British, storyteller",
    "onyx": "male, deep",
    "verse": "male, versatile",
}

GROK_VOICES = {
    "eve": "female, expressive (default)",
    "ara": "female, warm",
    "leo": "male, confident",
    "rex": "male, deep",
    "sal": "neutral, smooth",
}

# Well-known ElevenLabs premade voices (name → voice_id). Any other name is
# resolved live against your account's voice list; raw voice IDs also work.
ELEVEN_KNOWN = {
    "rachel": "21m00Tcm4TlvDq8ikWAM",
    "sarah": "EXAVITQu4vr4xnSDxMaL",
    "domi": "AZnzlk1XvdvUeBnXmlld",
    "elli": "MF3mGyEYCF7xYWbV9V6O",
    "antoni": "ErXwobaYiN019PkySvjV",
    "josh": "TxGEqnHWrfWFTfGW9XjX",
    "adam": "pNInz6obpgDQGcFmaJgB",
    "sam": "yoZ06aMxZJJ28mfd3POQ",
}

ENV_KEYS = {
    "openai": ["OPENAI_API_KEY"],
    "elevenlabs": ["ELEVENLABS_API_KEY", "XI_API_KEY"],
    "grok": ["XAI_API_KEY", "GROK_API_KEY"],
    "custom": ["CUSTOM_TTS_API_KEY"],
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

_pipe = None
_tty = None
_interrupted = False
_config = None
_tty_fd = None
_old_term = None


class ProviderError(Exception):
    pass


def _require_audio():
    if np is None or sd is None:
        raise ProviderError(
            "numpy/sounddevice not installed in this Python. "
            "Run: pip install sounddevice numpy   (and `pip install kokoro` for the local voice)"
        )


# ── config ──

def default_config() -> dict:
    return {
        "enabled": True,
        "provider": "kokoro",
        "voices": {
            "kokoro": "af_heart",
            "system": "",
            "openai": "marin",
            "elevenlabs": "rachel",
            "grok": "eve",
            "custom": "alloy",
        },
        "speed": 1.0,
        "volume": 1.0,
        "theme": "aurora",
        "chime": True,
        "min_chars": MIN_CHARS,
        "max_chars": MAX_CHARS,
        "window": WINDOW,
        "done_pause": DONE_PAUSE,
        "keys": {},
        "openai_model": "gpt-4o-mini-tts",
        "elevenlabs_model": "eleven_turbo_v2_5",
        "grok_language": "en",
        "custom": {"base_url": "", "model": "tts-1"},
    }


def load_config() -> dict:
    global _config
    if _config is not None:
        return _config
    cfg = default_config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            # migrate v0.1 config: top-level "voice" was the kokoro voice
            if "voice" in user and "voices" not in user:
                cfg["voices"]["kokoro"] = user["voice"]
        except (json.JSONDecodeError, OSError):
            pass
    _config = cfg
    set_theme(cfg.get("theme", "aurora"))
    return _config


def save_config(cfg: dict):
    global _config
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    cfg = {k: v for k, v in cfg.items() if k != "voice"}
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)  # config may hold API keys
    except OSError:
        pass
    _config = cfg


def get_key(cfg: dict, provider: str) -> str:
    key = (cfg.get("keys") or {}).get(provider, "")
    if key:
        return key
    for env in ENV_KEYS.get(provider, []):
        if os.environ.get(env):
            return os.environ[env]
    return ""


def current_voice(cfg: dict, provider: str) -> str:
    return (cfg.get("voices") or {}).get(provider) or default_config()["voices"].get(provider, "")


# ── terminal ──

def _restore_terminal():
    global _old_term, _tty_fd
    if _old_term is not None and _tty_fd is not None:
        try:
            termios.tcsetattr(_tty_fd, termios.TCSADRAIN, _old_term)
        except (termios.error, OSError):
            pass
        _old_term = None


def _clear_ui(t):
    t.write("\r\033[K\033[1A\033[K\033[1A\033[K")
    t.write(SHOW_CURSOR)
    t.flush()


def _handle_signal(sig, frame):
    global _interrupted
    _interrupted = True
    if sd is not None:
        sd.stop()
    _restore_terminal()
    if _tty:
        _clear_ui(_tty)
    sys.exit(0)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def get_tty():
    global _tty
    try:
        _tty = open("/dev/tty", "w")
    except OSError:
        _tty = sys.stderr
    return _tty


def term_width(t) -> int:
    try:
        return os.get_terminal_size(t.fileno()).columns
    except (OSError, ValueError):
        try:
            return shutil.get_terminal_size().columns
        except Exception:
            return 100


def visible_len(s: str) -> int:
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def _start_keypress_listener():
    """Background thread: any keypress sets _interrupted and stops audio."""
    global _tty_fd, _old_term

    def _listen():
        global _interrupted, _tty_fd, _old_term
        fd = None
        try:
            fd = os.open("/dev/tty", os.O_RDONLY)
            _tty_fd = fd
            _old_term = termios.tcgetattr(fd)
            tty.setraw(fd)
            while not _interrupted:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready:
                    os.read(fd, 1)
                    _interrupted = True
                    if sd is not None:
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


class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, out, text: str):
        self.out = out
        self.text = text
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            self.out.write(f"\r\033[K  {ACCENT}{frame}{RESET} {LABEL}{self.text}{RESET}")
            self.out.flush()
            i += 1
            self._stop.wait(0.08)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=0.5)
        self.out.write("\r\033[K")
        self.out.flush()


# ── chimes ──

def _chime(f0: float, f1: float, amp: float):
    if np is None or sd is None:
        return
    sr = 44100
    t = np.linspace(0, 0.08, int(sr * 0.08), False)
    freq = np.linspace(f0, f1, len(t))
    tone = np.sin(2 * np.pi * freq * t) * amp
    fade = np.minimum(t / 0.02, 1.0) * np.minimum((0.08 - t) / 0.02, 1.0)
    tone *= fade
    sd.play(tone.astype(np.float32), samplerate=sr)
    sd.wait()


def play_chime_start():
    _chime(600, 900, 0.15)


def play_chime_end():
    _chime(900, 600, 0.12)


# ── text processing ──

def is_mostly_code(text: str) -> bool:
    code_blocks = re.findall(r"```[\s\S]*?```", text)
    code_chars = sum(len(b) for b in code_blocks)
    return len(text) > 0 and (code_chars / len(text)) > 0.5


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#>|]", "", text)
    text = re.sub(r"\|[^\n]+\|", "", text)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"-{2,}", " ", text)
    return text.strip()


def fix_pronunciation(text: str) -> str:
    for term, replacement in PRONOUNCE.items():
        text = re.sub(rf"\b{re.escape(term)}\b", replacement, text)
    return text


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
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


def remap_timings(timings: list[tuple[float, float]], n: int) -> list[tuple[float, float]]:
    """Stretch/compress m word timings onto n display words (monotonic)."""
    m = len(timings)
    if m == n or m == 0 or n == 0:
        return timings
    out = []
    for i in range(n):
        a = min(int(i * m / n), m - 1)
        b = min(max(a, int((i + 1) * m / n) - 1), m - 1)
        out.append((timings[a][0], timings[b][1]))
    return out


# ── audio decoding ──

def pcm16_to_float(raw: bytes):
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def wav_to_float(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        audio = pcm16_to_float(raw)
    elif width == 4:
        audio = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif width == 1:
        audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ProviderError(f"unsupported wav sample width: {width}")
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def ffmpeg_decode(data: bytes, rate: int = SAMPLE_RATE):
    if not shutil.which("ffmpeg"):
        raise ProviderError(
            "this provider returned compressed audio and ffmpeg is not installed. "
            "Install it (macOS: brew install ffmpeg) and retry."
        )
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0",
         "-f", "f32le", "-ac", "1", "-ar", str(rate), "pipe:1"],
        input=data, capture_output=True,
    )
    if p.returncode != 0 or not p.stdout:
        raise ProviderError(f"ffmpeg failed to decode audio: {p.stderr.decode(errors='replace')[:200]}")
    return np.frombuffer(p.stdout, dtype=np.float32), rate


def decode_auto(data: bytes):
    """Decode unknown audio bytes: WAV natively, anything else via ffmpeg."""
    if data[:4] == b"RIFF":
        try:
            return wav_to_float(data)
        except (wave.Error, ProviderError):
            pass
    return ffmpeg_decode(data)


# ── HTTP ──

def http_request(url: str, headers: dict, body: dict | None = None,
                 method: str = "POST", timeout: int = 90) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode(errors="replace")[:300]
        except Exception:
            pass
        raise ProviderError(f"HTTP {e.code} from {url.split('/')[2]}: {detail or e.reason}")
    except urllib.error.URLError as e:
        raise ProviderError(f"network error reaching {url.split('/')[2]}: {e.reason}")


# ── providers ──
# Each synth_* returns (audio: float32 ndarray, sample_rate: int,
# word_timings: list[(start, end)] | None). Timings of None → estimated later.

def get_pipe():
    global _pipe
    if _pipe is None:
        try:
            from kokoro import KPipeline
        except ImportError:
            raise ProviderError(
                "kokoro is not installed. Run: pip install kokoro  "
                "— or switch providers: claude-voice provider system"
            )
        _pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    return _pipe


def synth_kokoro(text: str, voice: str, speed: float, cfg: dict):
    pipe = get_pipe()
    display_sentences = split_sentences(text)
    speech_sentences = split_sentences(fix_pronunciation(text))

    parts, timings = [], []
    offset = 0
    for i, sentence in enumerate(speech_sentences):
        chunks = []
        for result in pipe(sentence, voice=voice, speed=speed):
            chunks.append(result.audio.numpy())
        words = display_sentences[i].split() if i < len(display_sentences) else []
        if chunks:
            audio = np.concatenate(chunks)
            seg_dur = len(audio) / SAMPLE_RATE
            seg_start = offset / SAMPLE_RATE
            for ws, we in estimate_word_timings(words, seg_dur):
                timings.append((seg_start + ws, seg_start + we))
            parts.append(audio)
            offset += len(audio)
        else:
            seg_start = offset / SAMPLE_RATE
            timings.extend([(seg_start, seg_start)] * len(words))
    if not parts:
        raise ProviderError("kokoro produced no audio")
    return np.concatenate(parts), SAMPLE_RATE, timings


def synth_system(text: str, voice: str, speed: float, cfg: dict):
    speech = fix_pronunciation(text)
    rate_wpm = str(int(175 * speed))
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        if sys.platform == "darwin":
            cmd = ["say", "-o", path, "--data-format=LEI16@22050", "-r", rate_wpm]
            if voice:
                cmd += ["-v", voice]
            cmd.append(speech)
        else:
            binname = shutil.which("espeak-ng") or shutil.which("espeak")
            if not binname:
                raise ProviderError("no system TTS found (install espeak-ng)")
            cmd = [binname, "-w", path, "-s", rate_wpm]
            if voice:
                cmd += ["-v", voice]
            cmd.append(speech)
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode != 0:
            raise ProviderError(f"system TTS failed: {p.stderr.decode(errors='replace')[:200]}")
        with open(path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    audio, rate = wav_to_float(data)
    return audio, rate, None


def synth_openai(text: str, voice: str, speed: float, cfg: dict):
    key = get_key(cfg, "openai")
    if not key:
        raise ProviderError("no OpenAI API key. Set OPENAI_API_KEY or run: claude-voice key openai <key>")
    body = {
        "model": cfg.get("openai_model", "gpt-4o-mini-tts"),
        "input": text,
        "voice": voice,
        "response_format": "pcm",  # 24 kHz s16le mono
    }
    if speed != 1.0:
        body["speed"] = speed
    raw = http_request("https://api.openai.com/v1/audio/speech",
                       {"Authorization": f"Bearer {key}"}, body)
    return pcm16_to_float(raw), 24000, None


def _eleven_resolve_voice(name: str, key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9]{16,}", name) and not name.islower():
        return name  # already a voice ID
    low = name.lower()
    if low in ELEVEN_KNOWN:
        return ELEVEN_KNOWN[low]
    cache = {}
    if os.path.exists(ELEVEN_CACHE_PATH):
        try:
            with open(ELEVEN_CACHE_PATH) as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    if low not in cache:
        raw = http_request("https://api.elevenlabs.io/v1/voices",
                           {"xi-api-key": key}, method="GET")
        voices = json.loads(raw).get("voices", [])
        cache = {v["name"].lower(): v["voice_id"] for v in voices if v.get("voice_id")}
        os.makedirs(os.path.dirname(ELEVEN_CACHE_PATH), exist_ok=True)
        with open(ELEVEN_CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2)
    if low in cache:
        return cache[low]
    known = ", ".join(sorted(set(list(ELEVEN_KNOWN) + list(cache))))
    raise ProviderError(f"unknown ElevenLabs voice '{name}'. Available: {known}")


def _eleven_word_timings(alignment: dict) -> list[tuple[float, float]] | None:
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not chars or len(chars) != len(starts) or len(chars) != len(ends):
        return None
    timings, w_start, in_word = [], 0.0, False
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if in_word:
                timings.append((w_start, prev_end))
                in_word = False
        else:
            if not in_word:
                w_start, in_word = s, True
            prev_end = e
    if in_word:
        timings.append((w_start, prev_end))
    return timings or None


def synth_elevenlabs(text: str, voice: str, speed: float, cfg: dict):
    key = get_key(cfg, "elevenlabs")
    if not key:
        raise ProviderError("no ElevenLabs API key. Set ELEVENLABS_API_KEY or run: claude-voice key elevenlabs <key>")
    voice_id = _eleven_resolve_voice(voice, key)
    body = {"text": text, "model_id": cfg.get("elevenlabs_model", "eleven_turbo_v2_5")}
    if speed != 1.0:
        body["voice_settings"] = {"speed": max(0.7, min(1.2, speed))}
    base = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    headers = {"xi-api-key": key}
    try:
        raw = http_request(f"{base}?output_format=pcm_24000", headers, body)
        resp = json.loads(raw)
        audio = pcm16_to_float(base64.b64decode(resp["audio_base64"]))
        rate = 24000
    except ProviderError as e:
        if "output_format" not in str(e) and "HTTP 4" not in str(e):
            raise
        # some plans don't allow PCM output — fall back to mp3 + ffmpeg
        raw = http_request(f"{base}?output_format=mp3_44100_128", headers, body)
        resp = json.loads(raw)
        audio, rate = ffmpeg_decode(base64.b64decode(resp["audio_base64"]))
    timings = _eleven_word_timings(resp.get("alignment") or {})
    return audio, rate, timings


def synth_grok(text: str, voice: str, speed: float, cfg: dict):
    key = get_key(cfg, "grok")
    if not key:
        raise ProviderError("no xAI API key. Set XAI_API_KEY or run: claude-voice key grok <key>")
    body = {"text": text, "voice_id": voice, "language": cfg.get("grok_language", "en")}
    raw = http_request("https://api.x.ai/v1/tts",
                       {"Authorization": f"Bearer {key}"}, body)
    if raw[:1] == b"{":  # some APIs wrap audio in JSON
        try:
            resp = json.loads(raw)
            b64 = resp.get("audio") or resp.get("audio_base64") or ""
            if b64:
                raw = base64.b64decode(b64)
        except (json.JSONDecodeError, ValueError):
            pass
    audio, rate = decode_auto(raw)
    return audio, rate, None


def synth_custom(text: str, voice: str, speed: float, cfg: dict):
    custom = cfg.get("custom") or {}
    base_url = (custom.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ProviderError(
            "custom provider has no base_url. Edit ~/.config/claude-voice/config.json → "
            '"custom": {"base_url": "https://api.example.com/v1", "model": "tts-1"}'
        )
    key = get_key(cfg, "custom")
    body = {
        "model": custom.get("model", "tts-1"),
        "input": text,
        "voice": voice,
        "response_format": "wav",
    }
    if speed != 1.0:
        body["speed"] = speed
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    raw = http_request(f"{base_url}/audio/speech", headers, body)
    audio, rate = decode_auto(raw)
    return audio, rate, None


PROVIDERS = {
    "kokoro": {"fn": synth_kokoro, "needs_key": False, "local": True,
               "blurb": "local Kokoro 82M — free, private, no key"},
    "system": {"fn": synth_system, "needs_key": False, "local": True,
               "blurb": "your OS voice (say / espeak) — zero install"},
    "openai": {"fn": synth_openai, "needs_key": True, "local": False,
               "blurb": "OpenAI TTS (gpt-4o-mini-tts)"},
    "elevenlabs": {"fn": synth_elevenlabs, "needs_key": True, "local": False,
                   "blurb": "ElevenLabs — true word-level karaoke sync"},
    "grok": {"fn": synth_grok, "needs_key": True, "local": False,
             "blurb": "xAI Grok TTS (api.x.ai)"},
    "custom": {"fn": synth_custom, "needs_key": False, "local": False,
               "blurb": "any OpenAI-compatible /audio/speech endpoint"},
}

PROVIDER_ALIASES = {
    "11labs": "elevenlabs", "eleven": "elevenlabs", "xi": "elevenlabs",
    "xai": "grok", "local": "kokoro", "say": "system", "os": "system",
    "oai": "openai", "gpt": "openai",
}


def resolve_provider(name: str) -> str:
    name = name.lower().strip()
    name = PROVIDER_ALIASES.get(name, name)
    if name not in PROVIDERS:
        raise ProviderError(f"unknown provider '{name}'. Available: {', '.join(PROVIDERS)}")
    return name


def synthesize(provider: str, text: str, voice: str, speed: float, cfg: dict):
    _require_audio()
    audio, rate, timings = PROVIDERS[provider]["fn"](text, voice, speed, cfg)
    audio = np.asarray(audio, dtype=np.float32)
    volume = float(cfg.get("volume", 1.0))
    if volume != 1.0:
        audio = np.clip(audio * volume, -1.0, 1.0)
    return audio, rate, timings


# ── rendering ──

def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def provider_dot(provider: str) -> str:
    return f"{_rgb(PROVIDER_COLORS.get(provider, (150, 150, 150)))}●{RESET}"


def render_header(provider: str, voice: str, elapsed: float, total: float) -> str:
    t = f"{fmt_time(elapsed)} / {fmt_time(total)}"
    return (f"  {provider_dot(provider)} {BOLD}{provider}{RESET} {LABEL}·{RESET} "
            f"{voice or 'default'} {LABEL}·{RESET} {ACCENT}{t}{RESET}   "
            f"{DIM}any key skips{RESET}")


def render_karaoke(all_words: list[str], idx: int, window: int, width: int) -> str:
    total = len(all_words)
    w = window
    while True:
        start = max(0, idx - w)
        end = min(total, idx + w + 1)
        parts = []
        if start > 0:
            parts.append(f"{DIM}…{RESET}")
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
            parts.append(f"{DIM}…{RESET}")
        line = " ".join(parts)
        if visible_len(line) <= width - 4 or w <= 1:
            return line
        w -= 1


def mini_bar(current: int, total: int, width: int = 22) -> str:
    if total == 0:
        return ""
    frac = current / total
    cells = frac * width
    full = int(cells)
    half = "╸" if (cells - full) >= 0.5 and full < width else ""
    empty = width - full - (1 if half else 0)
    pct = int(frac * 100)
    return (f"{BAR_FILL}{'━' * full}{half}{BAR_EMPTY}{'━' * empty}{RESET} "
            f"{ACCENT}{pct:>3d}%{RESET} {LABEL}{current}/{total}{RESET}")


# ── core speak loop ──

def speak_and_highlight(text: str, provider: str | None = None, voice: str | None = None,
                        show_stats: bool = False) -> dict:
    global _interrupted
    cfg = load_config()
    provider = provider or cfg.get("provider", "kokoro")
    voice = voice or current_voice(cfg, provider)
    speed = float(cfg.get("speed", 1.0))
    window = cfg.get("window", WINDOW)
    done_pause = cfg.get("done_pause", DONE_PAUSE)
    chime = cfg.get("chime", True)
    _interrupted = False

    t0 = time.monotonic()
    all_words = text.split()
    total_words = len(all_words)

    out = get_tty()
    out.write(HIDE_CURSOR)
    spinner = Spinner(out, f"synthesizing · {provider} · {voice or 'default'}").start()
    try:
        audio, rate, timings = synthesize(provider, text, voice, speed, cfg)
    except Exception as e:
        spinner.stop()
        out.write(f"  {RED}✗{RESET} {LABEL}claude-voice ({provider}):{RESET} {e}\n")
        out.write(SHOW_CURSOR)
        out.flush()
        sys.stderr.write(f"claude-voice: {provider}: {e}\n")
        return {}
    spinner.stop()

    gen_time = time.monotonic() - t0
    audio_duration = len(audio) / rate

    if timings:
        timings = remap_timings(timings, total_words)
    if not timings or len(timings) != total_words:
        timings = estimate_word_timings(all_words, audio_duration)

    if chime:
        play_chime_start()

    _start_keypress_listener()

    width = term_width(out)
    out.write(f"{render_header(provider, voice, 0, audio_duration)}\n\n")
    out.flush()

    ttfa = time.monotonic() - t0
    playback_start = time.monotonic()
    sd.play(audio, samplerate=rate)

    for word_idx, (start, end) in enumerate(timings):
        if _interrupted:
            break
        elapsed = time.monotonic() - playback_start
        if elapsed < start:
            time.sleep(start - elapsed)
        if _interrupted:
            break
        elapsed = time.monotonic() - playback_start
        header = render_header(provider, voice, elapsed, audio_duration)
        karaoke = render_karaoke(all_words, word_idx, window, width)
        bar = mini_bar(word_idx + 1, total_words)
        out.write(f"\r\033[2A\033[K{header}\n\033[K  {karaoke}\n\033[K  {bar}")
        out.flush()

    sd.stop()
    _restore_terminal()
    total_time = time.monotonic() - t0

    if not _interrupted:
        header = render_header(provider, voice, audio_duration, audio_duration)
        bar = mini_bar(total_words, total_words)
        out.write(f"\r\033[2A\033[K{header}\n\033[K  {SPOKEN}done{RESET}\n\033[K  {bar}")
        out.flush()
        time.sleep(done_pause)
        if chime:
            play_chime_end()

    _clear_ui(out)

    stats = {
        "ttfa": ttfa,
        "gen_time": gen_time,
        "audio_duration": audio_duration,
        "total_time": total_time,
        "words": total_words,
        "chars": len(text),
        "voice": voice,
        "provider": provider,
    }
    if not show_stats:
        sys.stderr.write(
            f"claude-voice: ttfa={ttfa:.2f}s gen={gen_time:.2f}s "
            f"total={total_time:.2f}s words={total_words} provider={provider} voice={voice}\n"
        )
    if out is not sys.stderr:
        out.close()
    return stats


# ── hook input ──

def extract_hook_text(raw: str) -> str:
    """Pull the last assistant message from Stop-hook stdin JSON.

    Supports both the direct `last_assistant_message` field and newer
    payloads that only carry `transcript_path` (a JSONL session log).
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if not isinstance(data, dict):
        return raw
    msg = data.get("last_assistant_message")
    if msg:
        return msg
    path = data.get("transcript_path")
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                lines = f.readlines()
            for line in reversed(lines):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                content = (obj.get("message") or {}).get("content") or []
                texts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
                if texts:
                    return "\n".join(texts)
        except OSError:
            pass
    return ""


# ── setup / uninstall ──

def _invoker() -> str:
    exe = shutil.which("claude-voice")
    if exe:
        return "claude-voice"
    py = sys.executable
    if " " in py or " " in SCRIPT_PATH:
        return f'"{py}" "{SCRIPT_PATH}"'
    return f"{py} {SCRIPT_PATH}"


SLASH_TEMPLATE = """---
description: "Control voice output — on, off, status, provider <name>, voice <name>, speed <x>, volume <x>, theme <name>, voices"
allowed-tools: "Bash({invoker} slash:*)"
---

## Voice control result

!`{invoker} slash $ARGUMENTS`

Relay the result above to the user in one short line. It already reflects the
outcome — do not run any other commands. If it shows an error, briefly say how
to fix it.
"""


def _load_settings() -> dict:
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_settings(settings: dict):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def _is_our_hook(entry: dict) -> bool:
    s = str(entry)
    return "claude-voice" in s or "claude_voice" in s or "speak.py" in s


def cmd_setup(args=None):
    cfg = load_config()
    save_config(cfg)
    invoker = _invoker()

    settings = _load_settings()
    hooks = settings.get("hooks", {})
    stop_hooks = [h for h in hooks.get("Stop", []) if not _is_our_hook(h)]
    stop_hooks.append({
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": invoker,
            "timeout": 120,
            "async": True,
        }],
    })
    hooks["Stop"] = stop_hooks
    settings["hooks"] = hooks
    _save_settings(settings)

    os.makedirs(os.path.dirname(COMMAND_PATH), exist_ok=True)
    with open(COMMAND_PATH, "w") as f:
        f.write(SLASH_TEMPLATE.format(invoker=invoker))

    provider = cfg.get("provider", "kokoro")
    print(f"\n  {GREEN}✓{RESET} {BOLD}claude-voice v{VERSION} installed{RESET}")
    print(f"    {LABEL}Stop hook{RESET}      {SETTINGS_PATH}")
    print(f"    {LABEL}slash command{RESET}  {COMMAND_PATH}  {DIM}→ /voice in Claude Code{RESET}")
    print(f"    {LABEL}config{RESET}         {CONFIG_PATH}")
    print(f"    {LABEL}provider{RESET}       {provider} · {current_voice(cfg, provider)}")
    print(f"\n  Restart Claude Code, then try {CYAN}/voice status{RESET} inside it.")
    print(f"  Test now with {CYAN}claude-voice demo{RESET}\n")


def cmd_uninstall(args=None):
    settings = _load_settings()
    hooks = settings.get("hooks", {})
    before = len(hooks.get("Stop", []))
    hooks["Stop"] = [h for h in hooks.get("Stop", []) if not _is_our_hook(h)]
    removed = before - len(hooks["Stop"])
    if not hooks["Stop"]:
        del hooks["Stop"]
    settings["hooks"] = hooks
    _save_settings(settings)
    had_cmd = os.path.exists(COMMAND_PATH)
    if had_cmd:
        os.unlink(COMMAND_PATH)
    print(f"  {GREEN}✓{RESET} removed {removed} hook(s)"
          + (f" and {COMMAND_PATH}" if had_cmd else ""))
    print(f"  {DIM}config kept at {CONFIG_PATH} — delete it manually if you want a clean slate{RESET}")


# ── commands ──

def cmd_toggle_state(enable: bool | None, quiet: bool = False) -> str:
    cfg = load_config()
    cfg["enabled"] = (not cfg.get("enabled", True)) if enable is None else enable
    save_config(cfg)
    state = "on" if cfg["enabled"] else "off"
    if not quiet:
        color = GREEN if cfg["enabled"] else RED
        print(f"  claude-voice is now {color}{state}{RESET}")
    return state


def _key_status(cfg: dict, provider: str) -> str:
    if not PROVIDERS[provider]["needs_key"]:
        return "not needed"
    return "set" if get_key(cfg, provider) else "missing"


def _hook_installed() -> bool:
    return any(_is_our_hook(h) for h in _load_settings().get("hooks", {}).get("Stop", []))


def cmd_status(args=None):
    cfg = load_config()
    provider = cfg.get("provider", "kokoro")
    enabled = cfg.get("enabled", True)
    state = f"{GREEN}● on{RESET}" if enabled else f"{RED}● off{RESET}"
    keys = []
    for p in ("openai", "elevenlabs", "grok"):
        ok = bool(get_key(cfg, p))
        keys.append(f"{p} {GREEN}✓{RESET}" if ok else f"{DIM}{p} ✗{RESET}")
    print(f"\n  {BOLD}claude-voice{RESET} {LABEL}v{VERSION}{RESET}")
    print(f"  {LABEL}{'─' * 44}{RESET}")
    print(f"  {LABEL}state{RESET}      {state}")
    print(f"  {LABEL}provider{RESET}   {provider_dot(provider)} {provider}  {DIM}{PROVIDERS[provider]['blurb']}{RESET}")
    print(f"  {LABEL}voice{RESET}      {current_voice(cfg, provider) or 'system default'}")
    print(f"  {LABEL}speed{RESET}      {cfg.get('speed', 1.0)}×   {LABEL}volume{RESET} {int(cfg.get('volume', 1.0) * 100)}%")
    print(f"  {LABEL}theme{RESET}      {cfg.get('theme', 'aurora')}")
    print(f"  {LABEL}hook{RESET}       {'installed' if _hook_installed() else RED + 'not installed — run claude-voice setup' + RESET}")
    print(f"  {LABEL}/voice{RESET}     {'installed' if os.path.exists(COMMAND_PATH) else RED + 'not installed — run claude-voice setup' + RESET}")
    print(f"  {LABEL}keys{RESET}       {' · '.join(keys)}")
    print()


def cmd_provider(args):
    cfg = load_config()
    if not args:
        cur = cfg.get("provider", "kokoro")
        print(f"\n  {BOLD}Providers{RESET}\n")
        for name, meta in PROVIDERS.items():
            marker = f" {GREEN}◀ current{RESET}" if name == cur else ""
            keyinfo = ""
            if meta["needs_key"]:
                keyinfo = f"  {GREEN}key ✓{RESET}" if get_key(cfg, name) else f"  {YELLOW}key needed{RESET}"
            print(f"  {provider_dot(name)} {CYAN}{name:12s}{RESET} {meta['blurb']}{keyinfo}{marker}")
        print(f"\n  {DIM}switch: claude-voice provider <name>{RESET}\n")
        return
    name = resolve_provider(args[0])
    cfg["provider"] = name
    save_config(cfg)
    voice = current_voice(cfg, name)
    print(f"  {GREEN}✓{RESET} provider → {provider_dot(name)} {BOLD}{name}{RESET} (voice: {voice or 'system default'})")
    if PROVIDERS[name]["needs_key"] and not get_key(cfg, name):
        env = ENV_KEYS[name][0]
        print(f"  {YELLOW}!{RESET} no API key yet — set {CYAN}{env}{RESET} or run "
              f"{CYAN}claude-voice key {name} <key>{RESET}")


def cmd_voice(args):
    cfg = load_config()
    provider = cfg.get("provider", "kokoro")
    if not args:
        print(f"  current voice ({provider}): {CYAN}{current_voice(cfg, provider) or 'system default'}{RESET}")
        print(f"  {DIM}set: claude-voice voice <name> · list: claude-voice voices{RESET}")
        return
    voice = args[0]
    if provider == "kokoro" and voice not in KOKORO_VOICES:
        print(f"  {YELLOW}!{RESET} '{voice}' is not a known kokoro voice — setting anyway")
    cfg.setdefault("voices", {})[provider] = voice
    save_config(cfg)
    print(f"  {GREEN}✓{RESET} {provider} voice → {CYAN}{voice}{RESET}")


def _list_system_voices() -> dict:
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
            voices = {}
            for line in out.splitlines():
                m = re.match(r"^(\S+(?: \S+)*?)\s{2,}(\S+)\s+#\s*(.*)$", line)
                if m and m.group(2).startswith("en"):
                    voices[m.group(1)] = m.group(3)[:50]
            return dict(list(voices.items())[:14]) or {"(system default)": "leave voice unset"}
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"(system default)": "leave voice unset"}


def cmd_voices(args):
    cfg = load_config()
    target = resolve_provider(args[0]) if args else cfg.get("provider", "kokoro")
    current = current_voice(cfg, target)
    catalogs = {
        "kokoro": KOKORO_VOICES,
        "openai": OPENAI_VOICES,
        "grok": GROK_VOICES,
        "system": _list_system_voices(),
    }
    if target == "elevenlabs":
        catalog = {k.capitalize(): "premade voice" for k in ELEVEN_KNOWN}
        key = get_key(cfg, "elevenlabs")
        if key:
            try:
                raw = http_request("https://api.elevenlabs.io/v1/voices", {"xi-api-key": key}, method="GET")
                catalog = {v["name"]: (v.get("labels") or {}).get("description") or
                           ", ".join(filter(None, (v.get("labels") or {}).values())) or "voice"
                           for v in json.loads(raw).get("voices", [])}
            except ProviderError as e:
                print(f"  {YELLOW}!{RESET} couldn't fetch account voices ({e}) — showing built-ins")
    elif target == "custom":
        catalog = {"(any)": "voice names depend on your endpoint"}
    else:
        catalog = catalogs[target]
    print(f"\n  {BOLD}{target} voices{RESET}\n")
    for vid, desc in catalog.items():
        marker = f" {GREEN}◀{RESET}" if vid.lower() == (current or "").lower() else ""
        print(f"  {CYAN}{vid:18s}{RESET} {desc}{marker}")
    if target == "grok":
        print(f"\n  {DIM}xAI offers 80+ voices — any voice_id from docs.x.ai works here{RESET}")
    print(f"\n  {DIM}set: claude-voice voice <name>{RESET}\n")


def cmd_speed(args):
    cfg = load_config()
    if not args:
        print(f"  speed: {CYAN}{cfg.get('speed', 1.0)}×{RESET}")
        return
    try:
        val = float(args[0].rstrip("x×"))
        if not 0.25 <= val <= 4.0:
            raise ValueError
    except ValueError:
        print(f"  {RED}✗{RESET} speed must be a number between 0.25 and 4.0")
        return
    cfg["speed"] = val
    save_config(cfg)
    print(f"  {GREEN}✓{RESET} speed → {CYAN}{val}×{RESET}")


def cmd_volume(args):
    cfg = load_config()
    if not args:
        print(f"  volume: {CYAN}{int(cfg.get('volume', 1.0) * 100)}%{RESET}")
        return
    try:
        raw = args[0].rstrip("%")
        val = float(raw)
        if val > 2:  # treat as percentage
            val /= 100.0
        if not 0.0 <= val <= 2.0:
            raise ValueError
    except ValueError:
        print(f"  {RED}✗{RESET} volume must be 0-2 (or 0-200%)")
        return
    cfg["volume"] = val
    save_config(cfg)
    print(f"  {GREEN}✓{RESET} volume → {CYAN}{int(val * 100)}%{RESET}")


def cmd_theme(args):
    cfg = load_config()
    if not args:
        print(f"\n  {BOLD}Themes{RESET}  {DIM}(current: {cfg.get('theme', 'aurora')}){RESET}\n")
        for name, t in THEMES.items():
            sw = _rgb(t["accent"])
            print(f"  {sw}━━━━━{RESET} {name}")
        print(f"\n  {DIM}set: claude-voice theme <name>{RESET}\n")
        return
    name = args[0].lower()
    if name not in THEMES:
        print(f"  {RED}✗{RESET} unknown theme. Available: {', '.join(THEMES)}")
        return
    cfg["theme"] = name
    save_config(cfg)
    set_theme(name)
    print(f"  {GREEN}✓{RESET} theme → {HIGHLIGHT}{name}{RESET} {BAR_FILL}━━━━━━{RESET}")


def cmd_key(args):
    cfg = load_config()
    keyed = [p for p in PROVIDERS if PROVIDERS[p]["needs_key"] or p == "custom"]
    if not args:
        print(f"\n  {BOLD}API keys{RESET}\n")
        for p in keyed:
            stored = (cfg.get("keys") or {}).get(p, "")
            env_hit = next((e for e in ENV_KEYS.get(p, []) if os.environ.get(e)), None)
            if stored:
                src = f"{GREEN}✓ stored{RESET} {DIM}({stored[:6]}…){RESET}"
            elif env_hit:
                src = f"{GREEN}✓ env{RESET} {DIM}({env_hit}){RESET}"
            else:
                src = f"{DIM}✗ none{RESET}"
            print(f"  {CYAN}{p:12s}{RESET} {src}")
        print(f"\n  {DIM}set: claude-voice key <provider> <api-key>{RESET}\n")
        return
    provider = resolve_provider(args[0])
    if provider not in keyed:
        print(f"  {provider} doesn't use an API key")
        return
    if len(args) < 2:
        print(f"  usage: claude-voice key {provider} <api-key>")
        return
    cfg.setdefault("keys", {})[provider] = args[1]
    save_config(cfg)
    print(f"  {GREEN}✓{RESET} {provider} key saved to {CONFIG_PATH} {DIM}(chmod 600){RESET}")


def cmd_demo(args=None):
    cfg = load_config()
    provider = cfg.get("provider", "kokoro")
    voice = current_voice(cfg, provider)
    where = "local" if PROVIDERS[provider]["local"] else "cloud"
    print(f"\n  {BOLD}claude-voice demo{RESET}")
    print(f"  {LABEL}provider: {provider}  |  voice: {voice or 'default'}  |  {where}{RESET}\n")
    time.sleep(0.5)
    stats = speak_and_highlight(DEMO_TEXT, show_stats=True)
    if stats:
        print(f"\n  {GREEN}Demo complete.{RESET}")
        print(f"  {LABEL}ttfa={stats['ttfa']:.2f}s · gen={stats['gen_time']:.2f}s · "
              f"{stats['words']} words{RESET}\n")


def cmd_benchmark(args=None):
    cfg = load_config()
    provider = cfg.get("provider", "kokoro")
    voice = current_voice(cfg, provider)
    print(f"\n  {BOLD}claude-voice benchmark{RESET}")
    print(f"  {LABEL}provider: {provider}  |  voice: {voice or 'default'}{RESET}")
    print(f"  {LABEL}running 3 tests...{RESET}\n")

    labels = ["Short (2 words)", "Medium (15 words)", "Long (50 words)"]
    results = []
    for i, sentence in enumerate(BENCHMARK_SENTENCES):
        print(f"  {DIM}[{i + 1}/3] {labels[i]}...{RESET}", end="", flush=True)
        stats = speak_and_highlight(sentence, show_stats=True)
        if not stats:
            print(f"\r\033[K  {RED}[{i + 1}/3] {labels[i]} failed{RESET}")
            return
        results.append(stats)
        print(f"\r\033[K  {GREEN}[{i + 1}/3] {labels[i]}{RESET}  "
              f"ttfa={stats['ttfa']:.2f}s  gen={stats['gen_time']:.2f}s  "
              f"audio={stats['audio_duration']:.1f}s  total={stats['total_time']:.2f}s")
        time.sleep(0.3)

    avg_ttfa = sum(r["ttfa"] for r in results) / len(results)
    avg_gen = sum(r["gen_time"] for r in results) / len(results)
    print(f"\n  {LABEL}{'─' * 52}{RESET}")
    print(f"  {BOLD}Results{RESET}")
    print(f"  {LABEL}Avg time to first audio:{RESET}  {CYAN}{avg_ttfa:.2f}s{RESET}")
    print(f"  {LABEL}Avg generation time:{RESET}      {CYAN}{avg_gen:.2f}s{RESET}")
    print(f"  {LABEL}Provider / voice:{RESET}          {provider} / {voice or 'default'}")
    print(f"  {LABEL}{'─' * 52}{RESET}")
    print(f"\n  {DIM}Shareable:{RESET}")
    print(f"  claude-voice benchmark: ttfa={avg_ttfa:.2f}s avg_gen={avg_gen:.2f}s "
          f"provider={provider} voice={voice or 'default'}")
    print()


def cmd_doctor(args=None):
    cfg = load_config()

    def check(label, ok, hint=""):
        mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        extra = f"  {DIM}{hint}{RESET}" if hint and not ok else ""
        print(f"  {mark} {label}{extra}")
        return ok

    print(f"\n  {BOLD}claude-voice doctor{RESET} {LABEL}v{VERSION}{RESET}\n")
    check(f"python {sys.version.split()[0]} ({sys.executable})", sys.version_info >= (3, 10))
    check("numpy + sounddevice", np is not None and sd is not None,
          "pip install sounddevice numpy")
    try:
        import kokoro  # noqa: F401
        has_kokoro = True
    except ImportError:
        has_kokoro = False
    check("kokoro (local voice)", has_kokoro, "pip install kokoro — or use another provider")
    if sd is not None:
        try:
            ok_dev = sd.query_devices(kind="output") is not None
        except Exception:
            ok_dev = False
        check("audio output device", ok_dev, "no output device found")
    check("ffmpeg (decodes cloud mp3 audio)", bool(shutil.which("ffmpeg")), "brew install ffmpeg")
    if sys.platform != "darwin":
        check("espeak-ng (system provider)", bool(shutil.which("espeak-ng") or shutil.which("espeak")),
              "apt install espeak-ng")
    check("Stop hook installed", _hook_installed(), "claude-voice setup")
    check("/voice slash command installed", os.path.exists(COMMAND_PATH), "claude-voice setup")
    provider = cfg.get("provider", "kokoro")
    if PROVIDERS[provider]["needs_key"]:
        check(f"API key for current provider ({provider})", bool(get_key(cfg, provider)),
              f"claude-voice key {provider} <key>")
    print(f"\n  {LABEL}config:{RESET} {CONFIG_PATH}")
    print(f"  {LABEL}enabled:{RESET} {cfg.get('enabled', True)} · "
          f"{LABEL}provider:{RESET} {provider} · "
          f"{LABEL}voice:{RESET} {current_voice(cfg, provider) or 'default'}\n")


# ── /voice slash command backend (plain output, no ANSI) ──

def cmd_slash(args):
    cfg = load_config()

    def plain_status() -> str:
        provider = cfg.get("provider", "kokoro")
        state = "ON" if cfg.get("enabled", True) else "OFF"
        return (f"voice is {state} · provider: {provider} · voice: "
                f"{current_voice(cfg, provider) or 'default'} · speed: {cfg.get('speed', 1.0)}x "
                f"· volume: {int(cfg.get('volume', 1.0) * 100)}% · theme: {cfg.get('theme', 'aurora')}")

    if not args or args[0] in ("status", "state"):
        print(plain_status())
        return
    action, rest = args[0].lower(), args[1:]
    try:
        if action in ("on", "off", "toggle"):
            state = cmd_toggle_state({"on": True, "off": False}.get(action), quiet=True)
            print(f"voice output is now {state.upper()} ({plain_status()})")
        elif action == "provider" and rest:
            name = resolve_provider(rest[0])
            cfg["provider"] = name
            save_config(cfg)
            note = ""
            if PROVIDERS[name]["needs_key"] and not get_key(cfg, name):
                note = (f" — WARNING: no API key set. User must run: "
                        f"claude-voice key {name} <key> (or set {ENV_KEYS[name][0]})")
            print(f"provider switched to {name} (voice: {current_voice(cfg, name) or 'default'}){note}")
        elif action == "voice" and rest:
            provider = cfg.get("provider", "kokoro")
            cfg.setdefault("voices", {})[provider] = rest[0]
            save_config(cfg)
            print(f"{provider} voice set to {rest[0]}")
        elif action == "speed" and rest:
            cfg["speed"] = max(0.25, min(4.0, float(rest[0].rstrip("x×"))))
            save_config(cfg)
            print(f"speed set to {cfg['speed']}x")
        elif action == "volume" and rest:
            v = float(rest[0].rstrip("%"))
            cfg["volume"] = max(0.0, min(2.0, v / 100.0 if v > 2 else v))
            save_config(cfg)
            print(f"volume set to {int(cfg['volume'] * 100)}%")
        elif action == "theme" and rest and rest[0].lower() in THEMES:
            cfg["theme"] = rest[0].lower()
            save_config(cfg)
            print(f"theme set to {cfg['theme']}")
        elif action == "voices":
            target = cfg.get("provider", "kokoro")
            names = {"kokoro": list(KOKORO_VOICES), "openai": list(OPENAI_VOICES),
                     "grok": list(GROK_VOICES), "elevenlabs": [k.capitalize() for k in ELEVEN_KNOWN],
                     "system": ["(system default)"], "custom": ["(depends on endpoint)"]}[target]
            print(f"{target} voices: {', '.join(names)}")
        elif action == "providers":
            print("providers: " + ", ".join(f"{p} ({PROVIDERS[p]['blurb']})" for p in PROVIDERS))
        else:
            print("usage: /voice [on|off|toggle|status|provider <name>|voice <name>|"
                  "speed <x>|volume <x>|theme <name>|voices|providers]")
    except (ProviderError, ValueError) as e:
        print(f"error: {e}")


# ── main ──

COMMANDS = {
    "setup": cmd_setup,
    "uninstall": cmd_uninstall,
    "on": lambda a: cmd_toggle_state(True),
    "off": lambda a: cmd_toggle_state(False),
    "toggle": lambda a: cmd_toggle_state(None),
    "status": cmd_status,
    "provider": cmd_provider,
    "providers": lambda a: cmd_provider([]),
    "voice": cmd_voice,
    "voices": cmd_voices,
    "speed": cmd_speed,
    "volume": cmd_volume,
    "theme": cmd_theme,
    "key": cmd_key,
    "demo": cmd_demo,
    "benchmark": cmd_benchmark,
    "doctor": cmd_doctor,
    "slash": cmd_slash,
}


def main():
    load_config()

    if len(sys.argv) >= 2 and sys.argv[1] in COMMANDS:
        COMMANDS[sys.argv[1]](sys.argv[2:])
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="Claude Code TTS with karaoke word highlighting",
        usage="claude-voice [command] or claude-voice [options] [text]  "
              "(commands: " + ", ".join(COMMANDS) + ")",
    )
    parser.add_argument("text", nargs="*", help="Text to speak")
    parser.add_argument("--provider", "-p", default=None, help="TTS provider for this run")
    parser.add_argument("--voice", "-v", default=None, help="Voice for this run")
    parser.add_argument("--voices", action="store_true", help="List voices (legacy)")
    parser.add_argument("--long", action="store_true", help="No truncation — speak full text")
    parser.add_argument("--version", action="version", version=f"claude-voice {VERSION}")
    args = parser.parse_args()

    if args.voices:
        cmd_voices([args.provider] if args.provider else [])
        sys.exit(0)

    cfg = load_config()
    provider = resolve_provider(args.provider) if args.provider else None

    text = None
    hook_mode = False
    if args.text:
        text = " ".join(args.text)
    elif not sys.stdin.isatty():
        hook_mode = True
        if not cfg.get("enabled", True):
            sys.exit(0)
        raw = sys.stdin.read().strip()
        text = extract_hook_text(raw)
        if text and is_mostly_code(text):
            sys.exit(0)

    if not text or not text.strip():
        sys.exit(0)

    text = clean_for_speech(text)
    if not text:
        sys.exit(0)

    if hook_mode:
        if len(text) < cfg.get("min_chars", MIN_CHARS):
            sys.exit(0)
        max_chars = cfg.get("max_chars", MAX_CHARS)
        if not args.long and len(text) > max_chars:
            text = text[:max_chars].rsplit(" ", 1)[0] + "..."

    try:
        speak_and_highlight(text, provider=provider, voice=args.voice)
    except Exception as e:
        if _tty:
            _tty.write(SHOW_CURSOR)
            _tty.flush()
        if not hook_mode:
            print(f"  {RED}✗{RESET} {e}")
        sys.exit(0 if hook_mode else 1)


if __name__ == "__main__":
    main()
