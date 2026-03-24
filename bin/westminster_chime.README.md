# Westminster Chime Utility

`westminster_chime.py` is a standalone quarter-hour alert tool that derives the
Westminster melody from `/home/david/random/bin/westminster-chimes.mid` and
plays it through a simple audio backend.

## What it does

- Plays the quarter-hour Westminster phrases at `:15`, `:30`, `:45`, and `:00`
- Optionally strikes the hour count after the full-hour phrase
- Can run once for testing or stay resident in a scheduler loop
- Supports quiet-hour style gating with `--active-hours`
- Shows a transient control dialog during playback when a desktop display is available
- Persists dialog-edited schedule settings in `/home/david/.local/state/westminster_chime/settings.json`
- Supports separate scheduling for chime quarter marks and spoken announcements
- Falls back to an internal copy of the same note pattern if the MIDI file is
  missing or unreadable

## Quick examples

Preview the quarter-hour events without audio:

```bash
python3 /home/david/random/bin/westminster_chime.py --time 09:15 --dry-run
python3 /home/david/random/bin/westminster_chime.py --time 10:00 --dry-run
```

Play the current quarter if the current time is on a quarter boundary:

```bash
python3 /home/david/random/bin/westminster_chime.py
```

Force the control dialog on for testing:

```bash
python3 /home/david/random/bin/westminster_chime.py --time 09:15 --dialog on
```

Run continuously between 8am and 10pm:

```bash
python3 /home/david/random/bin/westminster_chime.py --watch --active-hours 08:00-22:00
```

Use a different MIDI file:

```bash
python3 /home/david/random/bin/westminster_chime.py --time 12:00 --midi-path /path/to/other.mid
```

## Backend notes

Backend selection defaults to `auto`, which prefers:

1. `ffplay`
2. `paplay`
3. `aplay`
4. `stdout`

You can force a backend with `--backend ffplay` or disable sound with
`--backend stdout`.

## Dialog behavior

- The playback dialog appears only while audio is active and dismisses itself once playback ends.
- `Dismiss` stops only the current chime.
- `Mute <N>m` stops the current chime and suppresses future ones until the mute window expires.
- `Save Schedule` updates persisted chime enablement, active hours, quarter marks, hour strike, and spoken-announcement hours.
- CLI flags still win when explicitly provided, so a cron entry that hardcodes `--active-hours` will override the saved dialog schedule.
- When no saved schedule exists yet, the dialog reflects the effective cron-derived chime and announcement schedule where it can infer it.

You can also open the same settings UI manually:

```bash
python3 /home/david/random/bin/westminster_chime.py --configure --dialog on
```

## Tray Icon

- `--tray` starts an XFCE-compatible status tray icon using `AyatanaAppIndicator3`.
- The tray menu can open the settings dialog, apply a temporary mute, clear mute, toggle chimes, or quit the tray process.
- Autostart entry: `/home/david/.config/autostart/westminster-chime-tray.desktop`

## Schedule Checks

- `--should-chime --time HH:MM` exits `0` when the current settings allow a chime at that time, otherwise `1`.
- `--should-announce --time HH:MM` exits `0` when the spoken announcement is allowed at that time, otherwise `1`.
- `presence_greeting_11speak.sh` now uses `--should-announce` before speaking, so the greeting can be scheduled independently from the bell.

## Cron example

If you prefer cron over the built-in watch loop:

```cron
*/15 * * * * /usr/bin/env python3 /home/david/random/bin/westminster_chime.py --active-hours 08:00-22:00
```

That setup keeps the logic in one place and lets cron handle wakeups, which is
less romantic than a tower mechanism but much easier to debug.
