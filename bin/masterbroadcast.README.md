# masterbroadcast (`masterbroadcast.py`)

Dedicated broadcast utility for speaking user-provided text into one or more Discord voice/stage channels, with optional live console transcription via message edits.

This is intentionally separate from `masterbot_v3.py`: no animation runtime, no state machine theatrics, just synthesize and broadcast.

## What It Does

- Accepts text from:
  - `--text`
  - `--file`
  - stdin pipe
- Splits long input into deterministic text chunks.
- Synthesizes each chunk with local `11speak.py` into MP3.
- Connects to a provided list of Discord voice/stage channels and plays chunk audio.
- Optionally edits existing Discord messages with a live "now speaking" chunk transcript.
- Optionally creates new console messages in specified text channels, then edits them live.
- By default, stops and restarts `masterbot.service` to interrupt/resume scheduled programming around the broadcast.

## Requirements

- Python 3.10+
- `discord.py` with voice support
- `ffmpeg` on `PATH`
- Local `11speak.py` present (default: `/home/david/random/bin/11speak.py`)
- Environment variables:
  - `DISCORD_TOKEN`
  - `ELEVENLABS_API_KEY`

Install baseline deps:

```bash
python3 -m pip install --user discord.py
sudo apt install ffmpeg
```

Export required env vars:

```bash
export DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN
export ELEVENLABS_API_KEY=YOUR_ELEVENLABS_KEY
```

## Discord Permissions / Intents

Your bot should have, at minimum:

- gateway intents:
  - `guilds`
  - `voice_states`
- channel permissions:
  - target voice/stage channels: `Connect`, `Speak`
  - console text channels (if using live edit/create): `View Channel`, `Send Messages`, `Read Message History`, `Manage Messages` (edit own messages is sufficient)

If the bot can see the guild but cannot connect/play, this is almost always a channel permission mismatch.

## Quick Start

Speak direct text to one voice channel:

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 \
  --text "Hello from masterbroadcast."
```

Speak from a file to multiple channels:

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 123456789012345678 \
  --file /home/david/random/message.txt
```

Pipe stdin:

```bash
cat /home/david/random/message.txt | \
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420
```

Run with MasterBot full preset (4 voice channels + status text channels):

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --preset masterbot-all \
  --reuse-last-console-messages \
  --text "Emergency broadcast mock test."
```

Run the built-in full flash-cycle mock script (same sequence used in the test run):

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --preset masterbot-all \
  --reuse-last-console-messages \
  --script-preset mock-emergency-flash \
  --chunk-chars 150
```

## Live Console Transcription

### Edit existing messages

Use `--console-messages` with refs in `channel_id:message_id` format:

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 \
  --file /home/david/random/message.txt \
  --console-messages 496375061134049294:1476684359665979533
```

### Create fresh console messages automatically

Use `--console-create-channels` with text channel IDs. The script posts a new starter message in each, then includes those in live edits:

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 \
  --text "Broadcast with live console mirrors." \
  --console-create-channels 496375061134049294 1386422860758913037
```

### Combine both

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 \
  --file /home/david/random/message.txt \
  --console-messages 496375061134049294:1476684359665979533 \
  --console-create-channels 1386422860758913037
```

## Interrupt / Resume MasterBot Daemon

Interrupt regular MasterBot runtime for the duration of the broadcast (this is now default behavior):

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 \
  --text "Emergency test override." \
  --interrupt-masterbot
```

Run without touching the daemon:

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 \
  --text "Normal announcement without daemon interruption." \
  --no-interrupt-masterbot
```

Manual control mode:

- stop before broadcast only:

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 \
  --text "Priority announcement." \
  --stop-masterbot
```

- start after broadcast only:

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 \
  --text "Resume notice." \
  --start-masterbot
```

If your unit has a different name:

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --channels 1109584160047247420 \
  --text "Service override test." \
  --interrupt-masterbot \
  --masterbot-service my-masterbot.service
```

## CLI Reference

```text
usage: masterbroadcast.py [-h] [--channels CHANNELS [CHANNELS ...]]
                          [--preset {masterbot-all} [{masterbot-all} ...]]
                          [--text TEXT | --file FILE | --script-preset {mock-emergency-flash}]
                          [--speak-bin SPEAK_BIN]
                          [--voice VOICE] [--model MODEL]
                          [--chunk-chars CHUNK_CHARS] [--timeout-s TIMEOUT_S]
                          [--console-messages CONSOLE_MESSAGES [CONSOLE_MESSAGES ...]]
                          [--console-create-channels CONSOLE_CREATE_CHANNELS [CONSOLE_CREATE_CHANNELS ...]]
                          [--reuse-last-console-messages]
                          [--sequential] [--interrupt-masterbot]
                          [--no-interrupt-masterbot]
                          [--stop-masterbot] [--start-masterbot]
                          [--masterbot-service MASTERBOT_SERVICE]
```

Arguments:

- `--channels`
  - Optional when `--preset` is used.
  - Voice/stage channel IDs.
  - Supports space-separated IDs and comma-separated groups.

- `--preset`
  - Optional channel preset(s).
  - `masterbot-all` loads:
    - voice targets from `runtime_config.voice_loop_preset_channel_ids` in `/home/david/random/logs/masterbot.db`
    - text console-create targets from `runtime_config.server_room_status_channel_ids`
  - If runtime config is unavailable, hardcoded fallback IDs are used.

- `--text`
  - Text payload to speak.
  - Mutually exclusive with `--file`.

- `--file`
  - UTF-8 text file path to speak.
  - Mutually exclusive with `--text`.

- `--script-preset`
  - Use a built-in payload script.
  - Current preset:
    - `mock-emergency-flash` (the full ACTIVE/STANDBY flash-cycle mock sequence)
  - Mutually exclusive with `--text` and `--file`.

- stdin input
  - If neither `--text` nor `--file` is passed, stdin is used when piped.

- `--speak-bin`
  - Path to `11speak.py`.
  - Default: `/home/david/random/bin/11speak.py`.

- `--voice`
  - Optional voice identifier passed through to `11speak.py`.

- `--model`
  - Optional model identifier passed through to `11speak.py`.

- `--chunk-chars`
  - Chunk target size for text splitting and `11speak` chunking.
  - Default: `600`.

- `--timeout-s`
  - Timeout budget for voice connect/play operations.
  - Default: `20.0`.

- `--console-messages`
  - Optional list of existing message refs as `channel_id:message_id`.
  - Those messages are edited chunk-by-chunk with live transcript.

- `--console-create-channels`
  - Optional list of text channel IDs where the tool should create new console messages.
  - Created messages are included in live transcript edits.

- `--reuse-last-console-messages`
  - Works with `--console-create-channels`.
  - Reuses the last compatible `MASTERBROADCAST LIVE` message per channel (if available) instead of creating a new one each run.
  - Compatibility check requires:
    - message authored by the bot
    - expected console signature lines (`=== MASTERBROADCAST LIVE ===` and `now speaking:`)
  - Falls back to creating a new message if cached message is missing/incompatible.

- `--sequential`
  - Default behavior plays to all connected targets in parallel per chunk.
  - With this flag, each chunk is played target-by-target sequentially.

- `--interrupt-masterbot`
  - Stop `masterbot.service` (or selected unit) before broadcast.
  - Start it again automatically after broadcast (even on most failures).
  - This is enabled by default.

- `--no-interrupt-masterbot`
  - Disable the default auto-stop/auto-start behavior.
  - Useful for low-priority announcements that should not interrupt normal loops.

- `--stop-masterbot`
  - Stop `masterbot.service` before broadcast.
  - No automatic resume unless `--start-masterbot` is also set.

- `--start-masterbot`
  - Start `masterbot.service` after broadcast completes.
  - Useful when paired with `--stop-masterbot` for explicit control.

- `--masterbot-service`
  - Override which user systemd unit to control.
  - Default: `masterbot.service`.

## Execution Model

1. Resolve payload input.
2. Split payload into chunks.
3. Login to Discord.
4. Resolve target voice/stage channels.
5. Resolve and/or create console messages.
6. Connect to target voice channels.
7. For each chunk:
   - synthesize chunk audio to temp MP3 via `11speak.py`
   - edit console messages with `chunk X/Y` and current text
   - play chunk on all targets (parallel or sequential)
8. Disconnect and exit.

This chunk-first model is what makes live transcription match spoken progress instead of dumping the full script upfront.

## Exit Codes

- `0`: success
- `1`: runtime errors/warnings occurred
- `2`: argument/env validation errors
- `130`: interrupted (Ctrl-C)

## Troubleshooting

### `DISCORD_TOKEN is not set` / `ELEVENLABS_API_KEY is not set`

Self-explanatory, unfortunately. Export them in the same shell/session running the script.

### Channel not found

- Bot cannot see that channel/guild.
- Wrong ID.
- Bot was removed from the server.

### Not a voice/stage channel

You passed a text/category/thread ID to `--channels`.

### Connect/playback failures

Common causes:

- Missing Discord permissions (`Connect`/`Speak`).
- Voice gateway handshake issues.
- `ffmpeg` missing from `PATH`.

Quick checks:

```bash
command -v ffmpeg
python3 -c "import discord; print(discord.__version__)"
```

### Console message edit failures

- Bot cannot access referenced text channel/message.
- Message deleted.
- Missing edit permissions.

Console update errors are reported but do not automatically abort voice playback.

### Synthesis failed

- `11speak.py` missing or failed.
- ElevenLabs key/quota/model issue.
- Empty normalized chunk input.

## Notes

- Temporary MP3 chunk files are created in a temp directory and removed on exit.
- Console transcript text is clipped before edit to avoid blowing Discord message limits.
- Console reuse cache file:
  - `/home/david/random/logs/masterbroadcast_console_messages.json`
- If you need persistent logs/audit trails, wrap invocation with shell logging or systemd journal capture.

## Related Files

- Script: `/home/david/random/bin/masterbroadcast.py`
- TTS backend wrapper: `/home/david/random/bin/11speak.py`
- Existing runtime bot: `/home/david/random/bin/masterbot_v3.py`
