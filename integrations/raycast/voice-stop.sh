#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Voice Stop Speaking
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 🤫
# @raycast.packageName Claude Voice

# Documentation:
# @raycast.description Interrupt whatever claude-voice is speaking right now. Suggested hotkey: Cmd+Ctrl+S
# @raycast.author claude-voice

"$HOME/.local/bin/claude-voice" stop | sed $'s/\033\[[0-9;]*m//g' | xargs
