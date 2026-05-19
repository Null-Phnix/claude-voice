-- claude-voice / Hammerspoon hotkey
--
-- Highlight any text in any app, press the hotkey, and the daemon speaks it.
-- The hotkey:
--   1. Sends Cmd+C to copy the current selection to the clipboard.
--   2. Waits briefly for the clipboard to update.
--   3. Runs `claude-voice clip`, which pbpastes and ships the text to the
--      daemon (bypassing mute / active-tty gating because it's user-initiated).
--
-- Install
-- -------
-- 1. Install Hammerspoon: `brew install --cask hammerspoon`, then grant it
--    Accessibility permissions in System Settings -> Privacy & Security.
-- 2. Paste this snippet into ~/.hammerspoon/init.lua.
-- 3. Open Hammerspoon and click "Reload Config" (menu bar icon).
-- 4. Edit CLAUDE_VOICE_BIN below to point at your install if it differs.
--
-- Default hotkey: Cmd+Shift+T (T for "talk"). Change `HOTKEY_*` below.

local CLAUDE_VOICE_BIN = "/usr/local/bin/claude-voice"
local HOTKEY_MODS      = { "cmd", "shift" }
local HOTKEY_KEY       = "t"
local COPY_WAIT_MS     = 80   -- raise if clipboard reads beat the copy

local function speakHighlighted()
    -- Snapshot the clipboard so we can restore it after speaking.
    local prevClip = hs.pasteboard.getContents()

    hs.eventtap.keyStroke({ "cmd" }, "c", 0)
    hs.timer.usleep(COPY_WAIT_MS * 1000)

    -- Fire and forget; the daemon handles playback.
    hs.task.new(CLAUDE_VOICE_BIN, function(exit, _stdout, stderr)
        if exit ~= 0 and stderr and #stderr > 0 then
            hs.alert.show("claude-voice clip failed: " .. stderr, 2)
        end
    end, { "clip" }):start()

    -- Restore the user's clipboard after a short delay so they don't lose
    -- whatever they had copied before pressing the hotkey.
    hs.timer.doAfter(0.4, function()
        if prevClip then hs.pasteboard.setContents(prevClip) end
    end)
end

hs.hotkey.bind(HOTKEY_MODS, HOTKEY_KEY, speakHighlighted)
hs.alert.show("claude-voice: " .. table.concat(HOTKEY_MODS, "+") .. "+" .. HOTKEY_KEY .. " -> speak selection", 1.5)
