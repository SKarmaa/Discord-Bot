#Requires AutoHotkey v2.0
#SingleInstance Force

; ============================================================
;  Bot Desktop Bridge
; ============================================================
;
; Protocol: the bot sends commands as "id|command" (e.g. "7|resume").
; AHK must echo that exact id back to /done once — and only once — the
; command has FULLY finished executing. This lets the bot tell the
; difference between "AHK picked it up" and "AHK actually finished it",
; which matters a lot for slow commands like clickplay.
;
; CHANGES FOR THE EPL / CAPTURE-CARD SETUP:
;   - STREAM_HOTKEY (Alt+S) is now bound in Discord to "Toggle Camera",
;     not "Toggle Stream/Screen Share". streamstart/streamstop (from
;     pc_control.py) now turn the camera on/off instead of sharing an
;     Edge window. Make sure Discord's keybind settings actually have
;     Alt+S assigned to Toggle Camera or this does nothing useful.
;   - Added MUTE_HOTKEY (Alt+H, "Toggle Mute") + a "togglemute" command,
;     wired to the new .mute bot command in pc_control.py.
;   - streamstart now sends an extra {Enter} after the hotkey to auto-
;     confirm Discord's "Turn on Camera" preview card, which can appear
;     even with camera-preview settings off. See ToggleCamera() below.

POLL_URL      := "http://127.0.0.1:9876/command"
DONE_URL      := "http://127.0.0.1:9876/done"
POLL_MS       := 500
STREAM_HOTKEY := "!s"   ; Discord keybind: Toggle Camera. ! = Alt, ^ = Ctrl, + = Shift
MUTE_HOTKEY   := "!h"   ; Discord keybind: Toggle Mute
JOIN_HOTKEY   := "!j"   ; Discord keybind: Join a voice channel
DISCON_HOTKEY := "!g"   ; Discord keybind: Disconnect

TraySetIcon("shell32.dll", 23)
A_TrayMenu.Delete()
A_TrayMenu.Add("Bot Desktop Bridge — Running", (*) => "")
A_TrayMenu.Add("Exit", (*) => ExitApp())
A_TrayMenu.Disable("Bot Desktop Bridge — Running")

F8:: {
    global STREAM_HOTKEY
    ToggleCamera(STREAM_HOTKEY)
}

SetTimer(PollCommand, POLL_MS)

PollCommand() {
    global POLL_URL, DONE_URL, STREAM_HOTKEY, MUTE_HOTKEY, JOIN_HOTKEY, DISCON_HOTKEY
    try {
        whr := ComObject("WinHttp.WinHttpRequest.5.1")
        whr.Open("GET", POLL_URL, false)
        whr.Send()
        raw := Trim(whr.ResponseText)
    } catch {
        return
    }

    if (raw = "")
        return

    ; Parse "id|command"
    pipePos := InStr(raw, "|")
    if (pipePos = 0) {
        ; Malformed — no id, nothing to report back. Ignore.
        return
    }
    cmdId := SubStr(raw, 1, pipePos - 1)
    cmd   := SubStr(raw, pipePos + 1)

    if (cmd = "streamstart" || cmd = "streamstop") {
        ToggleCamera(STREAM_HOTKEY)
    } else if (cmd = "togglemute") {
        DiscordHotkey(MUTE_HOTKEY)
    } else if (cmd = "refresh") {
        EdgeSendKey("{F5}")
    } else if (cmd = "fullscreen") {
        EdgeSendKey("{F11}")
    } else if (cmd = "closelink") {
        EdgeSendKey("^+k")
    } else if (cmd = "focusedge") {
        FocusEdge()
    } else if (cmd = "resume") {
        EdgeSendKey("{Space}")
    } else if (cmd = "join") {
        DiscordHotkey(JOIN_HOTKEY)
    } else if (cmd = "disconnect") {
        DiscordHotkey(DISCON_HOTKEY)
    } else if (cmd = "clickplay") {
        ClickPlayButton()
    } else if (cmd = "closeedge") {
        CloseEdge()
    } else if (SubStr(cmd, 1, 8) = "openlink") {
        url := Trim(SubStr(cmd, 9))
        Run("msedge.exe " . url)
    }

    ; Only NOW — after the action has actually finished — tell the bot.
    NotifyDone(DONE_URL, cmdId)
}

; Generic "send a Discord global keybind" helper. Discord's own window
; needs to NOT be the active foreground window for its global keybinds to
; register reliably, so we bounce focus to the taskbar first if Discord is
; currently active. Used for join / disconnect / mute — anything that's a
; plain, no-confirmation keypress.
DiscordHotkey(hotkey) {
    if WinActive("ahk_exe Discord.exe") {
        WinActivate("ahk_class Shell_TrayWnd")
        Sleep(400)
    }
    Send(hotkey)
    Sleep(300)
}

; Camera toggle needs a different focus dance than DiscordHotkey() above:
; turning the camera ON can pop up Discord's "Turn on Camera" preview card
; (a live self-view with a confirm button), and that confirm click only
; lands reliably if Discord is the actual foreground window when Enter is
; sent — sending Enter while some other window has focus just does
; nothing, or worse, types into whatever else is focused. So this one:
;   1. Focuses Discord for real (WinActivate + WinWaitActive, not just a
;      "soft" activate)
;   2. Sends the camera-toggle hotkey
;   3. Sends {Enter} to click through the preview card's default-focused
;      confirm button (harmless no-op if the card didn't appear)
;   4. Minimizes Discord back out of the way afterward
ToggleCamera(hotkey) {
    discordHwnd := WinExist("ahk_exe Discord.exe")
    if !discordHwnd {
        ; Discord isn't even running — nothing to focus/minimize, just try
        ; the hotkey in case it's a global one that still registers.
        Send(hotkey)
        return
    }

    WinActivate("ahk_id " . discordHwnd)
    WinWaitActive("ahk_id " . discordHwnd, , 2)
    Sleep(300)

    Send(hotkey)
    Sleep(500)   ; give the preview card time to render before confirming

    Send("{Enter}")
    Sleep(300)

    WinMinimize("ahk_id " . discordHwnd)
}

EdgeSendKey(key) {
    if !WinExist("ahk_exe msedge.exe")
        return
    WinActivate("ahk_exe msedge.exe")
    WinWaitActive("ahk_exe msedge.exe", , 2)
    Sleep(300)
    Send(key)
}

FocusEdge() {
    if WinExist("ahk_exe msedge.exe")
        WinActivate("ahk_exe msedge.exe")
}

; Force-closes Edge and waits (up to 5s) for the process to fully exit.
; Shared by ClickPlayButton (close-then-reopen) and CloseEdge (close-only).
; Only relevant to the WC/watchdgo workflow — EPL's capture-card setup
; doesn't touch Edge at all.
KillEdgeAndWait() {
    if WinExist("ahk_exe msedge.exe") {
        WinKill("ahk_exe msedge.exe")
        deadline := A_TickCount + 5000
        while ProcessExist("msedge.exe") && A_TickCount < deadline
            Sleep(200)
        Sleep(500)
    }
}

; WinActivate alone can report "success" without Windows ever firing a real
; foreground-change event (focus-stealing prevention blocks background
; processes from truly stealing focus). Discord's game-activity detector
; listens for that real event, so a "soft" activate can leave it thinking
; nothing changed. Toggling Always-On-Top forces a genuine foreground
; broadcast that Discord actually picks up.
ForceForeground(hwnd) {
    WinActivate("ahk_id " . hwnd)
    WinSetAlwaysOnTop(1, "ahk_id " . hwnd)
    Sleep(100)
    WinSetAlwaysOnTop(0, "ahk_id " . hwnd)
    WinActivate("ahk_id " . hwnd)
}

CloseEdge() {
    KillEdgeAndWait()
    Run("msedge.exe --new-window")
    Sleep(3000)

    edgeHwnd := WinExist("ahk_exe msedge.exe")
    if edgeHwnd {
        ForceForeground(edgeHwnd)
        WinWaitActive("ahk_id " . edgeHwnd, , 5)
    }
}

ClickPlayButton() {
    KillEdgeAndWait()
    Run("msedge.exe --new-window https://www.watchdgo.com/en")
    Sleep(5000)

    if !WinExist("ahk_exe msedge.exe")
        return

    edgeHwnd := WinExist("ahk_exe msedge.exe")
    ForceForeground(edgeHwnd)
    WinWaitActive("ahk_id " . edgeHwnd, , 5)
    Sleep(500)

    WinActivate("ahk_id " . edgeHwnd)
    Sleep(300)
    Send("^l")
    Sleep(400)
    SendText("javascript:")
    Sleep(150)

    js := "(function(){"
    js .= "var attempts=0;"
    js .= "var t=setInterval(function(){"
    js .= "attempts++;"
    js .= "var el=document.querySelector('a[href^=`"/en/live_events/`"]');"
    js .= "if(el){el.click();clearInterval(t);}"
    js .= "else if(attempts>=30){clearInterval(t);}"
    js .= "},1000);"
    js .= "})();"

    A_Clipboard := js
    Sleep(200)
    Send("^v")
    Sleep(300)
    Send("{Enter}")
    Sleep(32000)

    ForceForeground(edgeHwnd)
    Sleep(300)
}

NotifyDone(url, cmdId) {
    try {
        whr := ComObject("WinHttp.WinHttpRequest.5.1")
        whr.Open("POST", url . "?id=" . cmdId, false)
        whr.Send()
    }
}
