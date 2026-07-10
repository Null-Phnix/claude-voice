#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Voice On/Off
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 🔊
# @raycast.packageName Claude Voice

# Documentation:
# @raycast.description Toggle claude-voice TTS globally. Suggested hotkey: Cmd+Ctrl+V
# @raycast.author claude-voice

"$HOME/.local/bin/claude-voice" toggle | sed $'s/\033\[[0-9;]*m//g' | xargs
