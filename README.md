# claude-voice 🜏

> The other half of Claude Code's voice mode.

You talk to Claude with `/voice`. Now Claude talks back — with real-time karaoke word highlighting in your terminal. Local by default, cloud voices (OpenAI, ElevenLabs, xAI Grok) when you want them.

![claude-voice demo](demo.gif)

---

## Table of Contents

- [Why I Built This](#why-i-built-this)
- [What It Does](#what-it-does)
- [Install](#install)
- [Providers](#providers)
- [The /voice slash command](#the-voice-slash-command)
- [Commands](#commands)
- [Voices](#voices)
- [Config](#config)
- [How It Works](#how-it-works)
- [Daemon mode](#daemon-mode)
- [Multi-terminal routing](#multi-terminal-routing)
- [Highlight-and-speak hotkey](#highlight-and-speak-hotkey)
- [Current Pain Points](#current-pain-points)
- [End Goals](#end-goals--where-this-is-headed)
- [Requirements](#requirements)
- [License](#license)

---

## Why I Built This

Claude Code has `/voice` — you speak, it transcribes, Claude responds in text. But Claude never talks back. I'm staring at a terminal reading a 3-paragraph explanation when I could be *hearing* it while I keep working.

What I wanted: after every Claude response, automatically speak it aloud with word-by-word highlighting so I can follow along. Local. Free. Instant. No "would you like me to read this?" — just do it.

So I built a Claude Code **Stop hook**. After every response, the hook fires, strips markdown/code/URLs, generates audio, and renders karaoke-style highlighting to the terminal while it plays. Local Kokoro TTS (82M params, CPU) by default — and since v0.2, cloud providers when you want a nicer voice: OpenAI, ElevenLabs, xAI Grok, or any OpenAI-compatible endpoint.

---

## What It Does

- **Karaoke highlighting** — current word lit up, gradient around it, progress bar with %, word count and elapsed/total time
- **6 TTS providers** — local Kokoro (free, private), system voice, OpenAI, ElevenLabs, xAI Grok, or any OpenAI-compatible endpoint
- **True word sync on ElevenLabs** — uses their timestamps API for real word-level alignment, not estimation
- **`/voice` slash command** — control everything from inside Claude Code: `/voice off`, `/voice provider elevenlabs`, `/voice speed 1.2`
- **Background daemon** — the model loads once and stays in memory. Warm TTFA drops from ~6s to ~0.6s
- **Multi-terminal routing** — run Claude Code in five terminals at once and only the one you typed in speaks
- **Per-terminal mute** — `claude-voice mute` silences the current terminal only
- **Highlight-and-speak hotkey** — select any text in any app, press a key, and the daemon reads it back (Hammerspoon snippet included)
- **Smart filtering** — skips code-heavy responses, strips markdown/URLs/tables, fixes dev pronunciations for local engines
- **Interrupt on keypress** — press any key to stop immediately
- **Themes** — aurora, ember, violet, mint, mono
- **One-command setup** — `claude-voice setup` installs the hooks and the slash command

---

## Install

```bash
git clone https://github.com/Null-Phnix/claude-voice
cd claude-voice
python3 -m venv .venv && .venv/bin/pip install -e ".[local]"   # [local] = Kokoro TTS
.venv/bin/claude-voice setup
```

Cloud-only (no local model, tiny install): drop `[local]` and pick a provider:

```bash
.venv/bin/pip install -e .
.venv/bin/claude-voice provider openai
.venv/bin/claude-voice key openai sk-...
```

Restart Claude Code and every response will be spoken. Try `/voice status` inside Claude Code.

---

## Providers

| Provider | What it is | Key | Karaoke sync |
|---|---|---|---|
| `kokoro` (default) | Local Kokoro 82M — free, private, runs on CPU | none | estimated |
| `system` | Your OS voice (macOS `say` / espeak) — zero install | none | estimated |
| `openai` | OpenAI TTS (`gpt-4o-mini-tts`) | `OPENAI_API_KEY` | estimated |
| `elevenlabs` | ElevenLabs | `ELEVENLABS_API_KEY` | **true timestamps** |
| `grok` | xAI Grok TTS (`api.x.ai/v1/tts`) | `XAI_API_KEY` | estimated |
| `custom` | Any OpenAI-compatible `/audio/speech` endpoint (Groq, LocalAI, ...) | optional | estimated |

Switch any time: `claude-voice provider elevenlabs` or `/voice provider elevenlabs` inside Claude Code. Keys come from env vars or `claude-voice key <provider> <key>` (stored in the config file, chmod 600). Each provider remembers its own voice.

For `custom`, set the endpoint in `~/.config/claude-voice/config.json`:

```json
"custom": {"base_url": "https://api.groq.com/openai/v1", "model": "playai-tts"}
```

Cloud providers that return MP3 need `ffmpeg` on PATH (`brew install ffmpeg`).

---

## The /voice slash command

`claude-voice setup` installs `~/.claude/commands/voice.md`, so inside Claude Code you can type:

```
/voice off                 turn speech off globally
/voice on                  back on
/voice mute                mute just this terminal
/voice status              current state
/voice provider grok       switch provider
/voice voice eve           switch voice
/voice speed 1.3           faster
/voice theme violet        restyle the karaoke UI
/voice voices              list voices for the current provider
```

The command runs instantly (it executes before the prompt is sent) and Claude just relays the result.

---

## Commands

```bash
claude-voice setup              # install Stop + UserPromptSubmit hooks + /voice command
claude-voice uninstall          # remove all of it
claude-voice on | off | toggle  # enable / disable globally
claude-voice mute | unmute      # this terminal only
claude-voice status             # current state at a glance
claude-voice provider <name>    # switch TTS provider (no arg: list)
claude-voice voice <name>       # set voice for current provider
claude-voice voices [provider]  # list voices
claude-voice key <prov> <key>   # store an API key (no args: show key status)
claude-voice speed 1.2          # playback speed
claude-voice volume 80%         # volume
claude-voice theme ember        # UI theme
claude-voice clip               # speak the clipboard (bind to a hotkey)
claude-voice daemon-status      # warm-daemon state
claude-voice daemon-stop        # stop the daemon
claude-voice demo               # polished demo (for screen recording)
claude-voice benchmark          # latency stats
claude-voice doctor             # diagnose broken installs
claude-voice "some text"        # speak arbitrary text
claude-voice -p openai -v nova "text"   # one-off provider/voice
claude-voice --no-daemon "text" # bypass the daemon; speak in-process
```

---

## Voices

- **kokoro** — 12 voices: `af_heart` (default), `af_nova`, `am_adam`, `am_fenrir`, `bm_george`, `bf_emma`, ... (`claude-voice voices kokoro`)
- **openai** — `marin` (default), `cedar`, `nova`, `onyx`, `shimmer`, `coral`, `fable`, ...
- **elevenlabs** — `Rachel` (default), `Adam`, `Josh`, `Domi`, ... plus every voice on your account (fetched live), or any raw voice ID
- **grok** — `eve` (default), `ara`, `leo`, `rex`, `sal`, or any voice_id from docs.x.ai
- **system** — whatever your OS ships (`claude-voice voices system`)

---

## Config

`~/.config/claude-voice/config.json`:

```json
{
  "enabled": true,
  "provider": "kokoro",
  "voices": {"kokoro": "af_heart", "openai": "marin", "elevenlabs": "rachel", "grok": "eve"},
  "speed": 1.0,
  "volume": 1.0,
  "theme": "aurora",
  "chime": true,
  "use_daemon": true,
  "min_chars": 30,
  "max_chars": 1500,
  "keys": {}
}
```

---

## How It Works

1. Claude Code fires the **Stop hook** after every response
2. Hook reads the response text from stdin JSON (`last_assistant_message`, with a fallback that parses `transcript_path` on newer Claude Code versions)
3. Strips markdown, code blocks, URLs, tables — keeps only speakable text; skips responses that are mostly code
4. The hook hands the text to the **daemon** (spawning it if needed), which synthesizes with the active provider
5. Audio plays while word-by-word highlighting renders to the calling terminal's tty — ElevenLabs uses real character timestamps, everything else uses length-weighted estimation
6. Any keypress interrupts instantly; display cleans up after itself

---

## Daemon mode

Loading Kokoro takes ~6–10s, and the Stop hook fires a fresh Python process for every response. The daemon loads the model once and accepts requests over a Unix socket (`~/.cache/claude-voice/daemon.sock`), so warm TTFA drops to ~0.6s. It exits after 30 minutes idle and respawns on the next speak. Cloud providers go through it too — the hook client returns in ~50ms instead of blocking.

`claude-voice daemon-status`, `claude-voice daemon-stop`, or set `"use_daemon": false` in config to opt out.

---

## Multi-terminal routing

Run Claude Code in five terminals and only the one you're typing in speaks:

- A **UserPromptSubmit hook** claims the current terminal every time you send a prompt
- When a response finishes, the daemon only speaks if it finishes in the claimed terminal (claims expire after 10 minutes)
- `claude-voice mute` / `unmute` (or `/voice mute`) silences one terminal permanently without touching the others

---

## Hotkeys

[`integrations/raycast/`](integrations/raycast/) ships three Raycast script commands:

| Command | What it does | Suggested hotkey |
|---|---|---|
| Voice On/Off | toggle speech globally | ⌘⌃T |
| Voice Stop Speaking | interrupt current playback (`claude-voice stop`) | ⌘⌃S |
| Voice Speak Clipboard | read the clipboard aloud (`claude-voice clip`) | ⌘⌃C |

Setup: Raycast → Settings → Extensions → **+** → *Add Script Directory* → pick `integrations/raycast/`, then record a hotkey on each command. ⌘⌃ combos are chosen to stay clear of terminal multiplexers (tmux/herdr use plain ctrl/alt) and standard macOS shortcuts.

Prefer Hammerspoon? [`integrations/hammerspoon-snippet.lua`](integrations/hammerspoon-snippet.lua) binds highlight-and-speak (select text in any app, press the key, hear it via `claude-voice clip`).

---

## Current Pain Points

1. **It's one file and it's getting big** — `claude_voice.py` now handles six providers, a daemon, routing, rendering, and the CLI. Still "one file" by design, but it's pushing it.
2. **Code-heavy responses get skipped** — If a response is >50% inside code fences, the hook skips it. Correct, but the heuristic is crude and sometimes skips useful explanations that include code examples.
3. **Estimated karaoke timing on most providers** — Only ElevenLabs returns real word timestamps. Kokoro/OpenAI/Grok timing is length-weighted estimation; good, not perfect.
4. **Grok output format is undocumented** — xAI's TTS docs don't specify the audio container, so we sniff it (WAV natively, anything else via ffmpeg). Works, but it's a guess until they document it.
5. **Terminal compatibility** — Works on Kitty, Ghostty, Alacritty, iTerm2. Windows Terminal? Probably not.

---

## End Goals — Where This Is Headed

### Short Term
- **Better code detection** — distinguish "explanation with code examples" from "pure code response"
- **Streaming synthesis** — start speaking sentence 1 while sentence 2 is still generating (biggest remaining TTFA win)

### Medium Term
- **Integration with agent ecosystem** — when Deep Video Watcher generates a comprehension report, claude-voice can read it aloud; when Blackreach finishes research, it can narrate findings
- **Voice profiles per project** — coding voice (fast, clear) vs reading voice (warm, expressive) vs research voice (neutral, precise)
- **Pause/resume across sessions** — interrupt a long narration, come back later and resume from where you left off

### Long Term
- **Real-time conversation** — full duplex: I speak, Claude thinks, Claude speaks back, I interrupt, Claude adapts. True voice mode.
- **Integration with Claud-Ear** — when the music analysis tool runs, claude-voice narrates the results with the right musical terminology pronunciation

---

## Requirements

- Python 3.11+ (3.12 recommended for Kokoro/torch)
- `sounddevice`, `numpy` — plus `kokoro` for the local voice
- `ffmpeg` for cloud providers that return MP3
- macOS or Linux, any terminal with ANSI true color (Kitty, Ghostty, Alacritty, iTerm2, ...)

---

## License

MIT
