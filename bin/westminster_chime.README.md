# Westminster Chime Utility

`westminster_chime.py` is a standalone quarter-hour alert tool that derives the
Westminster melody from `/home/david/random/bin/westminster-chimes.mid` and
plays it through a simple audio backend.

## What it does

- Plays the quarter-hour Westminster phrases at `:15`, `:30`, `:45`, and `:00`
- Optionally strikes the hour count after the full-hour phrase
- Can run once for testing or stay resident in a scheduler loop
- Supports quiet-hour style gating with `--active-hours`
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

## Cron example

If you prefer cron over the built-in watch loop:

```cron
*/15 * * * * /usr/bin/env python3 /home/david/random/bin/westminster_chime.py --active-hours 08:00-22:00
```

That setup keeps the logic in one place and lets cron handle wakeups, which is
less romantic than a tower mechanism but much easier to debug.
