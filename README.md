# claude-voice

The other half of Claude Code's voice mode.

You talk to Claude with `/voice`. Now Claude talks back — with real-time karaoke word highlighting in your terminal. Local by default, cloud voices (OpenAI, ElevenLabs, xAI Grok) when you want them.

![claude-voice demo](demo.gif)

## What it does

Installs as a Claude Code **Stop hook**. After every response, Claude's text is spoken aloud while a karaoke-style highlight tracks the current word in your terminal.

- **Karaoke highlighting** — current word lit up, gradient around it, progress bar with %, word count and elapsed/total time
- **6 TTS providers** — local Kokoro (free, private), system voice, OpenAI, ElevenLabs, xAI Grok, or any OpenAI-compatible endpoint
- **True word sync on ElevenLabs** — uses their timestamps API for real word-level alignment, not estimation
- **`/voice` slash command** — control everything from inside Claude Code: `/voice off`, `/voice provider elevenlabs`, `/voice speed 1.2`
- **Smart filtering** — skips code-heavy responses, strips markdown/URLs/tables, fixes dev pronunciations for local engines
- **Interrupt on keypress** — press any key to stop immediately
- **Themes** — aurora, ember, violet, mint, mono
- **One-command setup** — `claude-voice setup` installs the hook and the slash command

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

## Commands

```bash
claude-voice setup              # install Stop hook + /voice slash command
claude-voice uninstall          # remove both
claude-voice on | off | toggle  # enable / disable
claude-voice status             # current state at a glance
claude-voice provider <name>    # switch TTS provider (no arg: list)
claude-voice voice <name>       # set voice for current provider
claude-voice voices [provider]  # list voices
claude-voice key <prov> <key>   # store an API key (no args: show key status)
claude-voice speed 1.2          # playback speed
claude-voice volume 80%         # volume
claude-voice theme ember        # UI theme
claude-voice demo               # polished demo (for screen recording)
claude-voice benchmark          # latency stats
claude-voice doctor             # diagnose broken installs
claude-voice "some text"        # speak arbitrary text
claude-voice -p openai -v nova "text"   # one-off provider/voice
```

## The /voice slash command

`claude-voice setup` installs `~/.claude/commands/voice.md`, so inside Claude Code you can type:

```
/voice off
/voice on
/voice status
/voice provider grok
/voice voice eve
/voice speed 1.3
/voice theme violet
/voice voices
```

The command runs instantly (it executes before the prompt is sent) and Claude just relays the result.

## Voices

- **kokoro** — 12 voices: `af_heart` (default), `af_nova`, `am_adam`, `am_fenrir`, `bm_george`, `bf_emma`, ... (`claude-voice voices kokoro`)
- **openai** — `marin` (default), `cedar`, `nova`, `onyx`, `shimmer`, `coral`, `fable`, ...
- **elevenlabs** — `Rachel` (default), `Adam`, `Josh`, `Domi`, ... plus every voice on your account (fetched live), or any raw voice ID
- **grok** — `eve` (default), `ara`, `leo`, `rex`, `sal`, or any voice_id from docs.x.ai
- **system** — whatever your OS ships (`claude-voice voices system`)

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
  "min_chars": 30,
  "max_chars": 1500,
  "keys": {}
}
```

## How it works

1. Claude Code fires the **Stop hook** after every response
2. Hook reads the response text from stdin JSON (`last_assistant_message`, with a fallback that parses `transcript_path` on newer Claude Code versions)
3. Strips markdown, code blocks, URLs, tables — keeps only speakable text; skips responses that are mostly code
4. The active provider synthesizes audio (spinner shows progress for cloud calls)
5. Audio plays while word-by-word highlighting renders to `/dev/tty` — ElevenLabs uses real character timestamps, everything else uses per-sentence length-weighted estimation
6. Any keypress interrupts instantly; display cleans up after itself

## Requirements

- Python 3.11+ (3.12 recommended for Kokoro/torch)
- `sounddevice`, `numpy` — plus `kokoro` for the local voice
- `ffmpeg` for cloud providers that return MP3
- macOS or Linux, any terminal with ANSI true color (Kitty, Ghostty, Alacritty, iTerm2, ...)

## License

MIT
