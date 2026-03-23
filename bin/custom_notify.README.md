# custom_notify

`custom_notify.py` is a local daemon + GUI + TTS notifier for two-way messaging.

- Script: `/home/david/random/bin/custom_notify.py`
- TTS backend: `/home/david/random/bin/11speak.py`
- Socket: `/tmp/clint_outside_notifier.sock`
- History JSON: `/home/david/.local/state/custom_notify/history.json`
- Audio cache dir: `/home/david/.cache/custom_notify/audio`

## Core Behavior

- Repeats a spoken alert on an interval until response or dismissal.
- Shows a GUI with response input (`Send Response`) and `Dismiss`.
- Supports terminal-side request/reply flows (`chat`, `ask`, `trigger`, `respond`).
- Persists history to JSON and auto-loads it on daemon startup.
- Timestamps history/replies in terminal format like `[3/6/2026 10:23:11 AM]`.
- Reuses cached audio for identical spoken text by default.
- Shows repeat-cycle speak events in terminal while waiting (unless quiet mode is enabled).

## Requirements

- Linux desktop session (Tkinter display access)
- Python 3
- `ELEVENLABS_API_KEY`
- `/home/david/random/bin/11speak.py`
- `ffplay`

## Default Mode

No subcommand starts interactive chat mode:

```bash
python3 /home/david/random/bin/custom_notify.py
```

Chat controls:
- `/subtext <text>`: set non-spoken subtitle for subsequent sends
- `/subtext`: clear subtitle
- `/regenerate on|off`: control per-message audio regeneration
- `/clear-history`: clear in-memory + JSON history
- `/dismiss`: detach terminal chat (daemon keeps running)
- `/quit` or `/exit`: stop daemon and exit
- `Ctrl-C`: stop daemon and exit

## Commands

```bash
python3 /home/david/random/bin/custom_notify.py --help
```

- `start`: start daemon in background
- `stop`: stop daemon
- `status`: show daemon state + recent history
- `dismiss`: dismiss active request/reminder (daemon stays running)
- `clear-history`: clear persisted + in-memory history
- `chat`: explicit interactive mode (default if omitted)
- `trigger`: non-blocking outbound message (still reply-capable)
- `ask`: blocking send-and-wait flow
- `respond`: respond from terminal to active request or request id

Flags:
- `--subtext <text>` on `trigger`/`ask`: GUI-visible subtitle only (not spoken)
- `--regenerate` on `trigger`/`ask`: force rebuild of cached audio
- `--quiet` on `chat`/`ask`: suppress non-essential wait/status/repeat logs

## `trigger` vs `ask`

- `trigger`: sends message and exits immediately.
- `ask`: sends message and waits for one outcome:
  - reply (`Response [timestamp]: ...`)
  - dismissal (`Request dismissed.`)
  - timeout (`Timed out waiting for response.`)

Both create reply-capable requests in GUI.

## Examples

Start daemon:
```bash
python3 /home/david/random/bin/custom_notify.py start
```

Non-blocking message:
```bash
python3 /home/david/random/bin/custom_notify.py trigger \
  "Emergency alert broadcast: Clint, David is outside." \
  --interval 10
```

Blocking request with subtext:
```bash
python3 /home/david/random/bin/custom_notify.py ask \
  "Please respond in the window." \
  --subtext "Visible in GUI only" \
  --interval 10 \
  --timeout 180
```

Quiet blocking request:
```bash
python3 /home/david/random/bin/custom_notify.py ask \
  "Please respond in the window." \
  --interval 10 \
  --quiet
```

Force audio regenerate once:
```bash
python3 /home/david/random/bin/custom_notify.py trigger \
  "Same spoken text" \
  --regenerate \
  --interval 10
```

Respond from terminal:
```bash
python3 /home/david/random/bin/custom_notify.py respond "On my way"
```

Dismiss active request without stopping daemon:
```bash
python3 /home/david/random/bin/custom_notify.py dismiss
```

Clear history:
```bash
python3 /home/david/random/bin/custom_notify.py clear-history
```

## History and Timestamps

History entries include:
- `sender` (`You`, `Them`, `System`)
- `text`
- unix timestamp `ts`

`status` and chat history print formatted timestamps.

During active waits, `chat` and `ask` print repeat cycle lines like:
```text
System [3/6/2026 12:45:01 PM]: Repeat spoke cycle 2.
```
Use `--quiet` to hide those live cycle lines.

Example `status` history line:
```text
- [3/6/2026 10:23:11 AM] Them: looks good
```

## Audio Cache Model

- Cache key: `sha256(spoken_message_text)`
- Path: `/home/david/.cache/custom_notify/audio/<hash>.mp3`
- Cache reused automatically for identical spoken text
- `--regenerate` bypasses reuse and rewrites cache file

## GUI Behavior

Window shows:
- spoken message text
- optional subtext (non-spoken)
- response input + `Send Response`
- `Dismiss`

`Send Response`:
- records reply
- hides window
- stops active repeat loop
- speaks confirmation: `Confirmation: your message has been delivered. Daemon still active.`

`Dismiss`:
- dismisses active request
- records dismissal event in history
- hides window

## Known Caveats

- If daemon starts in a non-GUI session, status may remain stopped or requests may not show a window.
- Request state transitions are asynchronous; very immediate status checks can briefly show transitional state.

## Troubleshooting

Daemon not up after `start`:
```bash
python3 /home/david/random/bin/custom_notify.py start
sleep 1
python3 /home/david/random/bin/custom_notify.py status
```

No audio:
- verify `ELEVENLABS_API_KEY`
- verify `11speak.py` works directly
- verify `ffplay` and audio sink

Clear active request and inspect state:
```bash
python3 /home/david/random/bin/custom_notify.py dismiss
python3 /home/david/random/bin/custom_notify.py status
```
