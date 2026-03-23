# MasterBot + MasterBroadcast Runtime Bundle README

This README describes the packaged runtime bundle intended to run the current MasterBot + MasterBroadcast stack with voiceover assets and operational state.

## Target Bundle

- `masterbot_masterbroadcast_runtime_bundle_2026-03-02_voiceovers-options.zip`

## What This Bundle Is For

- Running `masterbot_v3.py` as the primary Discord runtime.
- Running `masterbroadcast.py` for interrupt-style emergency/mock broadcasts.
- Preserving current runtime behavior for:
  - `--preset masterbot-all`
  - `--reuse-last-console-messages`
  - default interrupt/resume of `masterbot.service`
  - `--script-preset mock-emergency-flash`

## Included (High Level)

- Core scripts:
  - `random/bin/masterbot_v3.py`
  - `random/bin/masterbroadcast.py`
  - `random/bin/11speak.py`
- Script docs:
  - `random/bin/masterbot_v3.README.md`
  - `random/bin/masterbroadcast.README.md`
  - `random/bin/11speak.README.md`
- Service/runtime config:
  - `.config/systemd/user/masterbot.service`
  - `.config/masterbot.env`
- Runtime state files:
  - `random/logs/masterbot.db`
  - `random/logs/masterbot_cli_llm_cache.json`
  - `random/logs/masterbot_cli_memory.json`
  - `random/logs/masterbroadcast_console_messages.json`
  - `random/logs/sol_embeddings.pkl`
- Voiceovers (`masterbot_*.mp3`) needed by current workflows.
- Dependency map:
  - `random/masterbroadcast_option_dependencies.txt`

## Important Security Note

This bundle may include **live credentials and state**:

- `.config/masterbot.env` can contain real API keys/tokens.
- `random/logs/masterbot.db` and memory/cache files may include channel IDs, message IDs, and historical runtime context.

Treat this bundle as sensitive. Do not publish it publicly unless you sanitize secrets and state first.

## Required External Prerequisites

- Linux environment with:
  - `python3`
  - `ffmpeg` on `PATH`
  - installed Python dependencies used by scripts (`discord.py`, `openai`, and 11speak dependencies)
- User systemd available (`systemctl --user`)
- Discord bot/network access

## Install / Restore Steps

1. Extract zip to the same root layout (`/home/david`-style paths expected).
2. Verify permissions and Python environment.
3. Confirm `.config/masterbot.env` values.
4. Reload/start service:

```bash
systemctl --user daemon-reload
systemctl --user restart masterbot.service
systemctl --user status masterbot.service
```

5. Validate live logs:

```bash
journalctl --user -u masterbot.service -f
```

## Running MasterBroadcast

Full preset mock script:

```bash
python3 /home/david/random/bin/masterbroadcast.py \
  --preset masterbot-all \
  --reuse-last-console-messages \
  --script-preset mock-emergency-flash \
  --chunk-chars 150
```

This defaults to interrupting and resuming `masterbot.service`.

## Troubleshooting

- `DISCORD_TOKEN` missing:
  - check `.config/masterbot.env` and service environment loading.
- Voice handshake churn (`4006`, timeout):
  - transient Discord voice instability; retries/reconnects are built in.
- `ffmpeg` not found:
  - install `ffmpeg` and retry.
- Preset channel mismatch:
  - verify `random/logs/masterbot.db` has expected `runtime_config` IDs.

## Related Files

- `random/masterbroadcast_option_dependencies.txt`
- `random/bin/masterbot_v3.README.md`
- `random/bin/masterbroadcast.README.md`
