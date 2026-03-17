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
import sys
import termios
import threading
import time
import tty
import warnings

warnings.filterwarnings("ignore", category=FutureWarning, module=r"torch")
warnings.filterwarnings("ignore", category=UserWarning, module=r"torch")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"transformers")
warnings.filterwarnings("ignore", category=UserWarning, module=r"transformers")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"kokoro")
warnings.filterwarnings("ignore", category=UserWarning, module=r"kokoro")
warnings.filterwarnings("ignore", category=UserWarning, module=r"sounddevice")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"torch")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"transformers")
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


def _start_keypress_listener():
    """Start a background thread that sets _interrupted on any keypress."""
    global _tty_fd, _old_term

    def _listen():
        global _interrupted, _tty_fd, _old_term
        try:
            fd = os.open("/dev/tty", os.O_RDONLY)
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
            try:
                os.close(fd)
            except (OSError, UnboundLocalError):
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


def get_tty():
    global _tty
    try:
        _tty = open("/dev/tty", "w")
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


def speak_and_highlight(text: str, voice: str, show_stats: bool = False) -> dict:
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

    tty = get_tty()

    if chime:
        play_chime_start()

    # Start keypress listener — any key interrupts playback
    listener = _start_keypress_listener()

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


# ── commands ──

def cmd_setup():
    """Install claude-voice hook into Claude Code settings."""
    save_default_config()

    # Read existing settings, preserving file content
    raw = ""
    settings = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                raw = f.read()
            settings = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            pass

    # Check if hook already exists
    hooks = settings.get("hooks", {})
    stop_hooks = hooks.get("Stop", [])
    already = any(
        "claude-voice" in str(h) or "speak.py" in str(h)
        for entry in stop_hooks
        for h in entry.get("hooks", [])
    )

    if already:
        print(f"{GREEN}claude-voice is already installed.{RESET}")
        print(f"Config: {CONFIG_PATH}")
        return

    # Add the hook
    new_hook = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"python3 {SCRIPT_PATH}",
            "timeout": 60,
            "async": True,
        }]
    }
    stop_hooks.append(new_hook)
    hooks["Stop"] = stop_hooks
    settings["hooks"] = hooks

    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        f.write(json.dumps(settings, indent=2) + "\n")

    print(f"{GREEN}claude-voice installed.{RESET}")
    print(f"  Hook added to: {SETTINGS_PATH}")
    print(f"  Config at:     {CONFIG_PATH}")
    print(f"  Voice:         {load_config().get('voice', DEFAULT_VOICE)}")
    print(f"\nRestart Claude Code for the hook to take effect.")
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


def main():
    # Handle subcommands first
    if len(sys.argv) >= 2 and sys.argv[1] in ("setup", "demo", "benchmark", "on", "off"):
        cmd = sys.argv[1]
        if cmd == "setup":
            cmd_setup()
        elif cmd == "demo":
            cmd_demo()
        elif cmd == "benchmark":
            cmd_benchmark()
        elif cmd == "on":
            cmd_toggle(True)
        elif cmd == "off":
            cmd_toggle(False)
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="Claude Code TTS with word highlighting",
        usage="claude-voice [setup|demo|benchmark|on|off] or claude-voice [options] [text]",
    )
    parser.add_argument("text", nargs="*", help="Text to speak")
    parser.add_argument("--voice", "-v", default=None, help="Kokoro voice ID")
    parser.add_argument("--voices", action="store_true", help="List available voices")
    parser.add_argument("--long", action="store_true", help="No truncation — speak full text")
    args = parser.parse_args()

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

    try:
        speak_and_highlight(text, voice)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"claude-voice: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        _restore_terminal()
        if _tty:
            _tty.write(SHOW_CURSOR)
            _tty.flush()


if __name__ == "__main__":
    main()
