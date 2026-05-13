# claude-voice 🜏

> The missing half of Claude Code's voice mode.

You talk to Claude with `/voice`. Now Claude talks back — with real-time word-by-word highlighting in your terminal. Fully local. Zero API keys. One file.

![claude-voice demo](demo.gif)

---

## Table of Contents

- [Why I Built This](#why-i-built-this)
- [What It Does](#what-it-does)
- [Current Pain Points](#current-pain-points)
- [End Goals](#end-goals--where-this-is-headed)
- [Install](#install)
- [Commands](#commands)
- [Voices](#voices)
- [Config](#config)
- [How It Works](#how-it-works)
- [Benchmark](#benchmark)
- [Why Not VoiceMode / ElevenLabs / OpenAI TTS?](#why-not-voicemode--elevenlabs--openai-tts)
- [Requirements](#requirements)
- [License](#license)

---

## Why I Built This

Claude Code has `/voice` — you speak, it transcribes, Claude responds in text. But Claude never talks back. I'm staring at a terminal reading a 3-paragraph explanation when I could be *hearing* it while I keep working.

I tried ElevenLabs — $0.30 per million characters, API keys, cloud dependency, latency. I tried OpenAI TTS — $0.015 per 1K characters, same problems. I tried the official VoiceMode MCP — it's a tool Claude has to *choose* to call, not automatic.

What I wanted: after every Claude response, automatically speak it aloud with word-by-word highlighting so I can follow along. Local. Free. Instant. No "would you like me to read this?" — just do it.

So I built a Claude Code **Stop hook**. After every response, the hook fires, strips markdown/code/URLs, fixes pronunciation of dev terms, generates audio with Kokoro TTS (82M params, runs on CPU), and renders karaoke-style highlighting to the terminal while it plays.

One file. 21KB of Python. Zero API keys. It just works.

---

## What It Does

- **Karaoke highlighting** — current word lit up, gradient around it, progress bar with word count
- **Fully local** — no API keys, no cloud, no internet. Audio never leaves your machine
- **12 voices** — American/British, male/female. Warm, deep, polished, casual — pick your style
- **Smart filtering** — skips code-heavy responses, strips markdown/URLs/tables, fixes dev pronunciations (CLI, API, JSON, nginx, kubectl)
- **Interrupt on keypress** — press any key to stop immediately
- **One-command setup** — `claude-voice setup` adds the hook automatically

---

## Current Pain Points

These are the battles I'm actively fighting:

1. **It's one 21KB file and it's getting messy** — `claude_voice.py` handles TTS generation, audio playback, terminal rendering, markdown stripping, pronunciation fixes, config management, and the CLI. It's a monolith. Refactoring into modules would be cleaner but it's "one file" by design.

2. **Code-heavy responses get skipped** — If a response is >50% inside code fences, the hook skips it. This is correct (nobody wants to hear code read aloud) but sometimes it skips useful explanations that happen to include code examples. The heuristic is crude.

3. **espeak-ng is a system dependency** — Kokoro needs `espeak-ng` for phonemization. It's not a Python package, it's a system package. On some distros it's `espeak`, on others `espeak-ng`, and on macOS it's a Homebrew install away from working. This is the #1 support issue.

4. **macOS is "untested but likely works"** — I don't have a Mac at home to test on. The M3 MacBook at work is for other things. Linux users report it works great. macOS users report... mixed results. I need actual testing.

5. **No speed/pitch controls** — Some voices are too fast, some too slow. No way to adjust without editing the code. The Kokoro model supports speed but I haven't exposed it in the CLI.

6. **Terminal compatibility** — Works on Kitty, Ghostty, Alacritty. iTerm? Maybe. Windows Terminal? Probably not. The ANSI escape sequences for highlighting are standard but not universal.

---

## End Goals — Where This Is Headed

### Short Term (now → 3 months)
- **Speed control** — `--speed 0.8` or `--speed 1.2` flag for slower/faster speech
- **Better code detection** — distinguish "explanation with code examples" from "pure code response" so fewer useful explanations get skipped
- **macOS testing** — actually test on macOS and document any quirks

### Medium Term (3–6 months)
- **Integration with agent ecosystem** — when Deep Video Watcher generates a comprehension report, claude-voice can read it aloud; when Blackreach finishes research, it can narrate findings
- **Voice profiles per project** — coding voice (fast, clear) vs reading voice (warm, expressive) vs research voice (neutral, precise)
- **Pause/resume across sessions** — interrupt a long narration, come back later and resume from where you left off

### Long Term (6–12 months)
- **Real-time conversation** — not just "Claude responds, I hear it" but full duplex: I speak, Claude thinks, Claude speaks back, I interrupt, Claude adapts. True voice mode.
- **Integration with Claud-Ear** — when the music analysis tool runs, claude-voice narrates the results with the right musical terminology pronunciation

---

## Install

```bash
pip install kokoro sounddevice numpy
git clone https://github.com/Null-Phnix/claude-voice
cd claude-voice
python speak.py setup
```

That's it. Restart Claude Code and every response will be spoken.

---

## Commands

```bash
claude-voice setup              # install hook into Claude Code
claude-voice demo               # run a polished demo (for screen recording)
claude-voice benchmark          # measure latency, print shareable stats
claude-voice on                 # enable
claude-voice off                # disable (without removing the hook)
claude-voice --voices           # list all 12 voices
claude-voice --voice am_fenrir "text"   # speak with a specific voice
claude-voice --long "text"      # no truncation for long text
```

---

## Voices

| Voice | Description |
|-------|-------------|
| `af_heart` * | American female, warm & expressive |
| `af_nova` | American female, clear & professional |
| `af_alloy` | American female, smooth & neutral |
| `am_adam` | American male, natural |
| `am_fenrir` | American male, deep & strong |
| `am_onyx` | American male, smooth & confident |
| `bm_george` | British male, polished |
| `bm_daniel` | British male, warm |
| `bf_emma` | British female, clear |
| `bf_isabella` | British female, elegant |

\* default

---

## Config

Config lives at `~/.config/claude-voice/config.json`:

```json
{
  "voice": "af_heart",
  "min_chars": 30,
  "max_chars": 1500,
  "chime": true,
  "enabled": true
}
```

---

## How It Works

1. Claude Code fires the **Stop hook** after every response
2. Hook receives `last_assistant_message` as JSON on stdin
3. Strips markdown, code blocks, URLs, tables — keeps only speakable text
4. Skips if response is mostly code (>50% inside fences)
5. Fixes dev term pronunciation (CLI → "C L I", JSON → "jason", etc.)
6. Generates audio with Kokoro TTS, concatenates all sentences into one seamless buffer
7. Plays audio while rendering word-by-word highlighting to `/dev/tty`
8. Background thread listens for keypress — any key interrupts instantly
9. Cleans up display when done

---

## Benchmark

```
claude-voice benchmark: ttfa=0.93s avg_gen=0.59s voice=af_heart engine=kokoro-82M local=true
```

Warm TTFA (time to first audio) under 1 second. First run is ~6s due to model loading.

---

## Why Not VoiceMode / ElevenLabs / OpenAI TTS?

| | claude-voice | VoiceMode | ElevenLabs | OpenAI TTS |
|---|---|---|---|---|
| Word highlighting | Yes | No | No | No |
| Fully local | Yes | Optional | No | No |
| Zero API keys | Yes | Optional | No | No |
| Files | 1 | 100+ | - | - |
| Setup | One command | MCP server | Account + key | Account + key |
| Auto-speaks | Yes (Stop hook) | No (Claude must call tool) | No | No |
| Cost | Free | Free | $0.30/1M chars | $0.015/1K chars |

---

## Requirements

- Python 3.11+
- `kokoro`, `sounddevice`, `numpy`
- `espeak-ng` (system package for phonemization)
- Works on Linux. macOS support untested but likely works.
- Tested on Kitty, Ghostty, Alacritty. Any terminal with ANSI true color support.

---

## License

MIT
