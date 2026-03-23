# masterbot_v3 (`masterbot_v3.py`)

Main Discord runtime for MasterBot v3: stateful text rendering, reaction-driven CLI controls, telemetry panels, LLM-assisted analysis sections, and background voice loop orchestration.

## What It Handles

- Discord command runtime (`+` commands)
- Server-room ASCII console/status message rendering and edits
- Reaction control surface and interrupt queue
- Telemetry polling (system + Home Assistant-linked signals where configured)
- LLM bus integration for bounded analysis/narration output
- Voice loop tasks across preset voice channels
- Runtime config persistence and channel/message ID bookkeeping

## Requirements

- Python 3.10+
- `discord.py`
- `openai`
- `ffmpeg` on `PATH` (voice playback)
- Environment variables:
  - `DISCORD_TOKEN` (required)
  - `OPENAI_API_KEY` (required for LLM features initialized at startup)
  - `ELEVENLABS_API_KEY` (required for 11speak/voice generation workflows used by related scripts)

## Paths and Storage

- Script: `/home/david/random/bin/masterbot_v3.py`
- Logs: `/home/david/random/logs/masterbot.log`
- Runtime DB: `/home/david/random/logs/masterbot.db`
- Service unit: `/home/david/.config/systemd/user/masterbot.service`
- Optional env file for service: `/home/david/.config/masterbot.env`

## Run Manually

```bash
export DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN
export OPENAI_API_KEY=YOUR_OPENAI_API_KEY
python3 /home/david/random/bin/masterbot_v3.py
```

## Run via systemd (User Service)

```bash
systemctl --user daemon-reload
systemctl --user start masterbot.service
systemctl --user status masterbot.service
```

Enable on login:

```bash
systemctl --user enable masterbot.service
```

Live logs:

```bash
journalctl --user -u masterbot.service -f
```

## Runtime Config Notes

`masterbot_v3.py` persists dynamic runtime values in `masterbot.db` under `runtime_config`, including:

- `server_room_status_channel_ids`
- `server_room_status_pinned_message_ids`
- `voice_loop_preset_channel_ids`
- visitor role/guild defaults and related runtime toggles

These values are consumed by companion tooling (for example `masterbroadcast.py --preset masterbot-all`).

## Operational Notes

- If `DISCORD_TOKEN` or `OPENAI_API_KEY` is missing in the service environment, startup fails.
- Voice handshake interruptions (`4006`, timeout churn) can occur under heavy concurrent channel activity.
- Console and reaction flows are designed to be resilient, but Discord permission mismatches (edit/pin/reaction/voice) will degrade behavior per channel.
