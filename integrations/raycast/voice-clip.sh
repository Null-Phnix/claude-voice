#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Voice Speak Clipboard
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 🗣️
# @raycast.packageName Claude Voice

# Documentation:
# @raycast.description Read the current clipboard aloud (copy text first, or use with Cmd+C). Suggested hotkey: Cmd+Ctrl+C
# @raycast.author claude-voice

"$HOME/.local/bin/claude-voice" clip >/dev/null 2>&1 &
echo "speaking clipboard"
