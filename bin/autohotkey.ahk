XButton1::SoundPlay, C:\Users\david\Downloads\noooo.wav
XButton2::SoundPlay, C:\Users\david\Downloads\noooo.wav

$Volume_Up::
SoundGet, volume
Send {Volume_Up}
SoundSet, volume + 2
Return

$Volume_Down::
SoundGet, volume
Send {Volume_Down}
SoundSet, volume - 2
Return

; Toggle window transparency on the current window with Win+Escape.
#Esc::
    WinGet, TransLevel, Transparent, A
    if (TransLevel = OFF) {
        WinSet, Transparent, 200, A
    } else {
        WinSet, Transparent, OFF, A
    }
return




; Numpad1
NumpadEnd::SoundPlay, C:\Users\david\Downloads\noooo.wav
; Numpad2
; NumpadDown
; Numpad3
; NumpadPgdn
; Numpad4
; NumpadLeft
; Numpad5
; NumpadClear1
; Numpad6
; NumpadRight
; Numpad7
; NumpadHome
; Numpad8
; NumpadUp
; Numpad9
; NumpadPgUp
; Numpad0
; NumpadIns