# claude-voice

The missing half of Claude Code's voice mode.

You talk to Claude with `/voice`. Now Claude talks back — with real-time word-by-word highlighting in your terminal. Fully local. Zero API keys. One file.

![claude-voice demo](demo.gif)

## What it does

Installs as a Claude Code **Stop hook**. After every response, Claude's text is spoken aloud using [Kokoro TTS](https://github.com/hexgrad/kokoro) (82M params, runs on CPU) while a karaoke-style highlight tracks the current word in your terminal.

- **Karaoke highlighting** — current word lit up, gradient around it, progress bar with word count
- **Fully local** — no API keys, no cloud, no internet. Audio never leaves your machine
- **12 voices** — American/British, male/female. Warm, deep, polished, casual — pick your style
- **Smart filtering** — skips code-heavy responses, strips markdown/URLs/tables, fixes dev pronunciations (CLI, API, JSON, nginx, kubectl)
- **Interrupt on keypress** — press any key to stop immediately
- **One-command setup** — `claude-voice setup` adds the hook automatically

## Install

```bash
pip install kokoro sounddevice numpy
git clone https://github.com/Null-Phnix/claude-voice
cd claude-voice
python speak.py setup
```

That's it. Restart Claude Code and every response will be spoken.

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

## How it works

1. Claude Code fires the **Stop hook** after every response
2. Hook receives `last_assistant_message` as JSON on stdin
3. Strips markdown, code blocks, URLs, tables — keeps only speakable text
4. Skips if response is mostly code (>50% inside fences)
5. Fixes dev term pronunciation (CLI → "C L I", JSON → "jason", etc.)
6. Generates audio with Kokoro TTS, concatenates all sentences into one seamless buffer
7. Plays audio while rendering word-by-word highlighting to `/dev/tty`
8. Background thread listens for keypress — any key interrupts instantly
9. Cleans up display when done

## Benchmark

```
claude-voice benchmark: ttfa=0.93s avg_gen=0.59s voice=af_heart engine=kokoro-82M local=true
```

Warm TTFA (time to first audio) under 1 second. First run is ~6s due to model loading.

## Why not VoiceMode / ElevenLabs / OpenAI TTS?

| | claude-voice | VoiceMode | ElevenLabs | OpenAI TTS |
|---|---|---|---|---|
| Word highlighting | Yes | No | No | No |
| Fully local | Yes | Optional | No | No |
| Zero API keys | Yes | Optional | No | No |
| Files | 1 | 100+ | - | - |
| Setup | One command | MCP server | Account + key | Account + key |
| Auto-speaks | Yes (Stop hook) | No (Claude must call tool) | No | No |
| Cost | Free | Free | $0.30/1M chars | $0.015/1K chars |

## Requirements

- Python 3.11+
- `kokoro`, `sounddevice`, `numpy`
- `espeak-ng` (system package for phonemization)
- Works on Linux. macOS support untested but likely works.
- Tested on Kitty, Ghostty, Alacritty. Any terminal with ANSI true color support.

## License

MIT
