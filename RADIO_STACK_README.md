# Radio Stack README

This bundle contains the current terminal-driven radio stack under `random/bin/` plus the local interpretation layer under `radio-cortex/`.

The canonical operator config now lives at:

- `~/.config/radio/config.yaml`

That file centralizes the user-tunable defaults that used to be scattered across Bash constants, argparse defaults, environment-variable fallbacks, and the cortex worker YAML. CLI flags still override the config at runtime.

The stack now has five meaningful pieces:

1. Single-station `gqrx` control and monitoring.
2. Single-audio-source rolling archive jobs with metadata and transcripts.
3. Wideband nearby-channel simultaneous demodulation from one HackRF center frequency.
4. A shared session/ownership layer that keeps the SDR backends from stepping on each other quite so casually.
5. A `radio-cortex` layer that tails transcript logs, classifies activity with either a local or OpenAI-backed LLM, and writes structured event logs.

It is still not a polished product. It is, however, substantially less of a pile than it used to be.

## Included Files

Top-level documentation:

- `RADIO_STACK_README.md`

Main scripts:

- `bin/radio`
- `bin/radio_monitor.py`
- `bin/radio_transcribe.py`
- `bin/radio_archive.py`
- `bin/radio_multichannel.py`
- `bin/radio_session.py`
- `bin/radio_cortex.py`
- `bin/radio_rds.py`
- `bin/radio_scan.py`
- `bin/radio_wfm.py`

Cortex layer:

- `radio-cortex/config.yaml`
- `radio-cortex/llama_worker.py`
- `radio-cortex/prompt_templates.py`
- `radio-cortex/event_engine.py`
- `radio-cortex/memory_graph.py`
- `radio-cortex/working_memory.py`
- `radio-cortex/sol_narrator.py`
- `radio-cortex/utils/tail.py`
- `radio-cortex/utils/time.py`
- `radio-cortex/utils/filters.py`

Shared config loader:

- `bin/radio_config.py`

## High-Level Architecture

### 1. `radio`

`bin/radio` is the main wrapper CLI.

It currently exposes:

- headless `gqrx` start/stop/tune/status/log
- monitor UI
- live transcription
- RDS helper
- archive jobs
- multichannel jobs
- session status
- cortex start/status/log/stop

For the older single-station path, `radio` manages:

- `~/.local/state/radio/gqrx.pid`
- `~/.local/state/radio/xvfb.pid`
- `~/.local/state/radio/gqrx.log`
- `~/.config/gqrx/hackrf-fm.conf`

### 2. `radio_session.py`

This is the session brain.

It keeps shared state in:

- `~/.local/state/radio/session.json`
- `~/.local/state/radio/session.lock`

It knows:

- which backend owns the primary SDR path
- which auxiliary jobs exist
- whether the current `gqrx` owner looks healthy or degraded
- and after roughly 30 seconds of sustained degraded `gqrx` remote health, it treats that owner as dead so the SDR does not remain blocked forever by a half-alive socket corpse
- enough state to reject obvious collisions before the hardware gets dragged into an avoidable argument

### 3. `radio_monitor.py`

Terminal monitor for the single-station `gqrx` path.

It shows:

- frequency/mode/signal/gains
- `gqrx` remote status
- optional live transcription state
- effective transcription backend/model
- cortex backend/model/mode plus latest inference summary
- decoder chatter from `multimon-ng`
- decoder raw log path and likely-hit log path

When `gqrx` is present, the monitor and transcriber now prefer the live `GQRX` sink-input stream instead of the whole desktop monitor, so system audio does not get transcribed just because it had the misfortune of existing nearby.

Decoder logs:

- `~/.local/state/radio/decoder.log`
- `~/.local/state/radio/decoder_hits.log`

### 4. `radio_transcribe.py`

Live transcription worker.

Backends currently supported:

- `vosk`
- `openai`
- `nemo`
- `auto`

`auto` prefers NeMo when a compatible runtime exists, then OpenAI if `OPENAI_API_KEY` is present, then Vosk.

`--all-local` forces local ASR selection and avoids the OpenAI path.

Input modes:

- PulseAudio monitor/source via `parec`
- raw PCM file or FIFO via `--input-file`

That FIFO mode is what lets the multichannel backend feed per-channel audio directly into separate transcribers.

### 5. `radio_archive.py`

Single-source archive job runner.

It records one PulseAudio source or monitor into rolling FLAC segments and optionally runs timestamped transcription alongside it.

Per-job outputs live under:

- `~/.local/state/radio/archive/jobs/<name>/`

Typical files:

- `audio/*.flac`
- `events.jsonl`
- `transcripts.jsonl`
- `status.json`
- `config.json`
- `launcher.log`

This is for one already-demodulated audio source. It is not a multi-frequency SDR recorder.

### 6. `radio_multichannel.py`

Wideband nearby-channel simultaneous backend.

This is the part that respects the real SDR constraint:

- one HackRF center frequency
- several nearby stations inside the same usable capture window
- one optional live playback channel
- one recording path per channel
- one transcript sidecar per channel

It does not use `gqrx`.

Instead it:

- opens HackRF directly through `osmosdr`
- channelizes each requested offset from the center
- demods each channel independently
- writes raw PCM into FIFOs
- feeds those FIFOs into:
  - `ffmpeg` segment recorders
  - `radio_transcribe.py` workers

Per-job outputs live under:

- `~/.local/state/radio/multichannel/jobs/<name>/`

Per-channel outputs live under:

- `channels/<channel-name>/audio/*.flac`
- `channels/<channel-name>/transcripts.jsonl`

### 7. `radio-cortex`

This is the interpretation layer.

It tails transcript files from archive jobs, multichannel jobs, and an optional single-station monitor transcript file, batches recent transcript lines, runs either local Llama-3.2-1B or an explicit OpenAI backend, and emits:

- `~/.local/state/radio/events.jsonl`
- `~/.local/state/radio/sol_log.txt`
- `~/.local/state/radio/working_memory.json`
- `~/.local/state/radio/cortex/status.json`
- `~/.local/state/radio/cortex/worker.log`

Current behavior:

- only `final` transcript lines are considered
- obvious junk is filtered before interpretation
- classification is local-model driven
- the event schema now supports discrete FM/event-bearing content with `type=event` plus optional `entity`, `location`, `date`, and `event_type`
- event records now also carry a more specific `content_type` layer such as `weather_report`, `song`, `discussion_topic`, `concert`, or `station_identification`, plus optional `title`, `artist`, `topic`, and `inferred`
- weather/advisory/station-id events now also carry `detailed_summary` and `full_text`, so the event log can retain the full narrated content instead of only the compressed headline
- adjacent `weather`, `advisory`, and `station_id` chunks can be coalesced into one event window instead of being logged as separate fragments of the same NOAA loop
- adjacent `song`, `discussion_topic`, and `event` chunks can now also be coalesced over shorter windows so FM does not spray one tiny event per line of lyric or banter
- slower enrichment now runs on a 3-minute inference window instead of continuously guessing on every fragment
- the monitor can manually force that slower pass with `i`, and doing so resets the inference timer
- event keywords are grounded from transcript text instead of trusting the model to invent them with confidence
- in-process event memory tracks crude loop hints like `cycle_detected`, `cycle_length_seconds`, and `phase`
- persistent working memory survives restarts and tracks recent summaries, known entities, dominant type, last change reason, suppression counts, and coarse cycle length hints
- decoder hits from `~/.local/state/radio/decoder_hits.log` are also tailed, with DTMF tones promoted into structured `type=dtmf` events instead of being left as raw log shrapnel
- DTMF events carry short sequence state, basic control-vs-sequence classification, and nearest recent audio context when one is available
- novelty suppression now uses both recent in-process memory and the persistent expectation layer, which is slightly less goldfish-like
- the OpenAI backend now uses Structured Outputs for classification instead of raw text JSON optimism, so fields like `title`, `artist`, `topic`, `entity`, and `date` are more likely to survive intact
- obviously generic one-line lyric fragments and empty discussion scraps can now be suppressed instead of bloating `events.jsonl`
- narration is deterministic when running through `llama-cli`, and only uses model-written narration if `llama_cpp` is available
- cortex now also supports an explicit OpenAI LLM backend for classification/narration, using the same `responses.create(...)` then chat-completions fallback pattern as other local tools
- `radio cortex --all-local` forces the worker back onto the local backend path even if the config default points at OpenAI
- failed `llama_cpp` imports are cached so a missing dependency does not get retried every poll cycle like an especially persistent bad idea
- default transcript globs include `~/.local/state/radio/monitor_transcript.jsonl` for future single-station transcript ingestion
- weather-mode transcript batches are now source-aware: NOAA archive paths and live weather-band tuning bias cortex toward `weather`, `advisory`, `station_id`, or `emergency` instead of letting forecast text wander off and declare itself a song

This keeps the interesting part local and the fragile part less fragile.

Cortex caveats worth remembering:

- the single-station monitor transcript file is only a watched path today; the monitor still needs to write it for cortex to consume it
- cycle detection is intentionally crude and currently meant as a hint, not a canonical NOAA forecast model
- DTMF correlation is intentionally local and heuristic; it links tones to nearby audio events, not to the private thoughts of the broadcast automation rack
- if `llama_cpp` is unavailable, the worker cleanly falls back to `llama-cli`
- `model.backend: auto` still means local by default; OpenAI is opt-in via `model.backend: openai` so the stack does not quietly start spending money because a key happened to exist
- persistent working memory is intentionally simple; it is a durable operator memory, not a full semantic knowledge graph
- monitor metadata and station-change logging is intentionally debounced now; the goal is operator-relevant state transitions, not a diary of every transient `offline` wobble
- the slower inference pass is intentionally not continuous anymore; expect `last inferred` to stay blank until a window flush or manual trigger

## Single-Station Workflow

Start headless `gqrx`:

```bash
radio start 103.7 fm
```

Tune:

```bash
radio tune 162.55 weather
radio tune 121.5 air
radio tune 14.25 usb
```

Monitor:

```bash
radio monitor
radio monitor --transcribe --transcribe-backend openai
radio monitor --transcribe --all-local
radio monitor --transcribe --transcribe-backend nemo --transcribe-model nvidia/parakeet-tdt-0.6b-v2
```

Direct transcription:

```bash
radio transcribe --backend openai
radio transcribe --all-local
radio transcribe --backend nemo --nemo-model nvidia/parakeet-tdt-0.6b-v2
radio transcribe --backend vosk --partials
```

RDS:

```bash
radio rds 103.7 --seconds 30
```

## Archive Workflow

Rolling archive:

```bash
radio archive start wx --cache-hours 6 --transcribe --transcribe-backend openai
```

Permanent archive:

```bash
radio archive start station-a --permanent --transcribe
```

Inspect:

```bash
radio archive status wx
radio archive log wx --lines 50
```

Stop:

```bash
radio archive stop wx
```

## Multichannel Workflow

Example:

```bash
radio multichannel start wxband \
  --center 162.55 \
  --channel main:162.55:nfm:play \
  --channel alt:162.4:nfm \
  --transcribe \
  --transcribe-backend openai
```

Meaning:

- `--center 162.55`
  One HackRF center frequency.
- `main:162.55:nfm:play`
  A channel named `main`, tuned at `162.55 MHz`, demodulated as narrow FM, also sent to live playback.
- `alt:162.4:nfm`
  Another nearby narrow-FM channel recorded and transcribed simultaneously.

Inspect:

```bash
radio multichannel status wxband
radio multichannel log wxband --lines 80
```

Stop:

```bash
radio multichannel stop wxband
```

## Cortex Workflow

Start it:

```bash
radio cortex start
radio cortex start --all-local
```

Inspect it:

```bash
radio cortex status
radio cortex log
tail -f ~/.local/state/radio/events.jsonl
tail -f ~/.local/state/radio/sol_log.txt
```

Stop it:

```bash
radio cortex stop
```

Example weather-monitoring flow:

```bash
radio tune 162.55 weather
radio archive start wx --cache-hours 1 --transcribe --transcribe-backend openai
radio cortex start
tail -f ~/.local/state/radio/events.jsonl
```

## Supported Multichannel Modes

Current channel mode parser supports:

- `nfm`
- `wfm`
- `am`

Notes:

- only one channel can be tagged `:play`
- channels must fit inside the practical RF window around the chosen center frequency
- validation is conservative by design
- current multichannel default per-channel audio sample rate is `50000`
- `wfm` multichannel currently expects `--sample-rate 50000`

## Constraints That Actually Matter

### One HackRF Is Not Magic

One HackRF can simultaneously support multiple channels only when those channels fit inside the same RF capture window around a shared center frequency.

That means:

- `162.55 MHz` and `162.4 MHz` can work together
- `103.7 MHz` and `162.55 MHz` cannot work together in the current hardware setup

That separation is about `58.85 MHz`, which is well outside the discussed usable window.

### Single-Station Path vs Multichannel Path

The `gqrx` path and the multichannel path are different backends.

Single-station path:

- one demodulated audio stream
- controlled through `gqrx`
- easier for ad hoc listening

Multichannel path:

- direct SDR access
- no `gqrx`
- better for simultaneous nearby station capture

### `gqrx` Brittleness

`gqrx` remote-control startup is still somewhat flaky after direct SDR backend testing.

Observed behavior:

- process can be up
- PID/Xvfb files exist
- `radio status` may show `mode: unknown` and `freq: unknown`
- logs show startup without the remote socket coming back cleanly

Translation: the radio stack is functional, but the single-station `gqrx` path may still need extra cleanup after direct HackRF jobs.

## Runtime Requirements

### General

- Linux desktop with PulseAudio or PipeWire Pulse compatibility
- `ffmpeg`
- `parec`
- `multimon-ng`
- `xdotool`
- `tesseract`
- ImageMagick tools (`import`, `convert`) for the OCR snapshot path

### Python / SDR

Most wrapper scripts use system Python, but the SDR multichannel path uses:

- `/home/david/.venvs/radio/bin/python`

This matters because the system Python currently has an `osmosdr`/NumPy mismatch while the radio venv works.

### ASR Backends

OpenAI:

- `OPENAI_API_KEY` must be set
- current default model: `gpt-4o-transcribe`

Vosk:

- `vosk` must be installed

NeMo:

- requires `torch` plus `nemo.collections.asr`
- current code supports a dedicated runtime, defaulting to:
  - `~/.venvs/radio-asr/bin/python`
- code path exists, but runtime installation may still be incomplete depending on the machine state

### Cortex / Local Llama

Current local model path:

- `~/.cache/models/Llama-3.2-1B-Instruct-Q4_K_M.gguf`

Current runtime behavior:

- works through local `llama-cli`
- optionally uses `llama_cpp` if installed
- output is intentionally conservative and JSON-oriented

## Output Layout

Single-station / `gqrx` state:

- `~/.local/state/radio/gqrx.log`
- `~/.local/state/radio/gqrx.pid`
- `~/.local/state/radio/xvfb.pid`
- `~/.local/state/radio/decoder.log`
- `~/.local/state/radio/decoder_hits.log`

Session layer:

- `~/.local/state/radio/session.json`
- `~/.local/state/radio/session.lock`

Archive jobs:

- `~/.local/state/radio/archive/jobs/<job>/...`

Multichannel jobs:

- `~/.local/state/radio/multichannel/jobs/<job>/...`

Cortex:

- `~/.local/state/radio/cortex/status.json`
- `~/.local/state/radio/cortex/worker.log`
- `~/.local/state/radio/events.jsonl`
- `~/.local/state/radio/sol_log.txt`
- `~/.local/state/radio/working_memory.json`

## Logging Contract

This is the “what actually gets written” view, not the more romantic “what the UI seems to know” view.

| Path | Producer | Retention | Schema / contents | Authority |
| --- | --- | --- | --- | --- |
| `~/.local/state/radio/gqrx.log` | `radio` / headless `gqrx` launcher | until overwritten on next start | raw `gqrx` stdout/stderr | telemetry |
| `~/.local/state/radio/gqrx.pid` | `radio` | runtime only | plain PID | control-plane |
| `~/.local/state/radio/xvfb.pid` | `radio` | runtime only | plain PID | control-plane |
| `~/.local/state/radio/xvfb.log` | `radio` / `Xvfb` | append per run until truncated by restart logic | raw Xvfb stdout/stderr | telemetry |
| `~/.local/state/radio/session.json` | `radio_session.py` | rolling current state + short history | JSON with `primary`, `aux`, `active_backend`, `history`, health/degraded state | authoritative for SDR ownership |
| `~/.local/state/radio/session.lock` | `radio_session.py` | runtime only | lockfile | control-plane |
| `~/.local/state/radio/decoder.log` | `radio_monitor.py` | append until manually cleared/rotated | raw `multimon-ng` output plus session markers | raw telemetry |
| `~/.local/state/radio/decoder_hits.log` | `radio_monitor.py` | append until manually cleared/rotated | heuristic subset of decoder lines that look meaningful | heuristic telemetry |
| `~/.local/state/radio/events.jsonl` | `radio_monitor.py` and `radio-cortex` | append until manually cleared/rotated | JSONL event stream; monitor metadata/station-change events plus cortex interpreted events, DTMF events, novelty/memory fields, `content_type`, weather `detailed_summary` / `full_text` | shared high-level event log, useful but not canonical RF truth |
| `~/.local/state/radio/monitor_transcript.jsonl` | nobody yet | n/a | watched path only; not currently written by monitor | nonexistent / reserved |
| `~/.local/state/radio/sol_log.txt` | `radio-cortex` | append until manually cleared/rotated | one-line narrative/operator summaries | derived narrative |
| `~/.local/state/radio/working_memory.json` | `radio-cortex` | persistent until manually cleared | JSON memory state: recent summaries, known entities, DTMF history/patterns, stats, coarse cycle hints | authoritative for cortex expectation state |
| `~/.local/state/radio/cortex/status.json` | `radio-cortex` | overwritten in place | live worker status: pid, backend, counters, pending items, working-memory snapshot | authoritative for cortex runtime state |
| `~/.local/state/radio/cortex/manual_inference.trigger` | `radio_monitor.py` | overwritten on demand | one-token file used to force a window inference flush | control-plane |
| `~/.local/state/radio/cortex/worker.pid` | `radio-cortex` | runtime only | plain PID | control-plane |
| `~/.local/state/radio/cortex/worker.log` | `radio-cortex` launcher | append per daemon lifetime | detached worker stdout/stderr | telemetry |
| `~/.local/state/radio/archive/jobs/<job>/audio/*.flac` | `radio_archive.py` / `ffmpeg` | rolling by `cache_hours` unless `--permanent` | segmented audio | authoritative archived audio |
| `~/.local/state/radio/archive/jobs/<job>/events.jsonl` | `radio_archive.py` | append for life of job dir | JSONL job events: session start/stop, ffmpeg/transcriber start/exit/logs, `radio_state`, segment deletion, transcript finals | authoritative archive job event log |
| `~/.local/state/radio/archive/jobs/<job>/transcripts.jsonl` | `radio_archive.py` + `radio_transcribe.py` | append for life of job dir | JSONL transcript records with backend/model and transcriber events | authoritative archive transcript sidecar |
| `~/.local/state/radio/archive/jobs/<job>/status.json` | `radio_archive.py` | overwritten in place | job status JSON: source, backend/model, segment counts, last transcript, stopped time | authoritative archive job status |
| `~/.local/state/radio/archive/jobs/<job>/config.json` | `radio_archive.py` | persistent | JSON job config | authoritative job config snapshot |
| `~/.local/state/radio/archive/jobs/<job>/pid` | `radio_archive.py` | runtime only | plain PID | control-plane |
| `~/.local/state/radio/archive/jobs/<job>/launcher.log` | `radio_archive.py` launcher | append per detached launch | launcher stdout/stderr | telemetry |
| `~/.local/state/radio/archive/jobs/<job>/worker.log` | nominally `radio_archive.py` | currently mostly decorative | path exists in code/status, but the worker does not meaningfully write to it today | misleading / low value |
| `~/.local/state/radio/multichannel/jobs/<job>/events.jsonl` | `radio_multichannel.py` | append for life of job dir | JSONL job events: session start/stop, ffmpeg/transcriber starts, ffmpeg logs, segment open/delete | authoritative multichannel job event log |
| `~/.local/state/radio/multichannel/jobs/<job>/status.json` | `radio_multichannel.py` | overwritten in place | job status JSON with center freq, channels, per-channel segment counts and last transcript text | authoritative multichannel job status |
| `~/.local/state/radio/multichannel/jobs/<job>/config.json` | `radio_multichannel.py` | persistent | JSON job config | authoritative job config snapshot |
| `~/.local/state/radio/multichannel/jobs/<job>/pid` | `radio_multichannel.py` | runtime only | plain PID | control-plane |
| `~/.local/state/radio/multichannel/jobs/<job>/launcher.log` | `radio_multichannel.py` launcher | append per detached launch | launcher stdout/stderr | telemetry |
| `~/.local/state/radio/multichannel/jobs/<job>/channels/<channel>/audio/*.flac` | `radio_multichannel.py` / `ffmpeg` | rolling by `cache_hours` unless `--permanent` | segmented per-channel audio | authoritative archived audio |
| `~/.local/state/radio/multichannel/jobs/<job>/channels/<channel>/transcripts.jsonl` | `radio_multichannel.py` + `radio_transcribe.py` | append for life of job dir | per-channel JSONL transcript sidecar with backend/model and transcriber events | authoritative per-channel transcript |
| `~/.local/state/radio/multichannel/jobs/<job>/channels/<channel>/record.pcm` | `radio_multichannel.py` | runtime only | FIFO, not durable | transient plumbing |
| `~/.local/state/radio/multichannel/jobs/<job>/channels/<channel>/transcribe.pcm` | `radio_multichannel.py` | runtime only | FIFO, not durable | transient plumbing |

Things that are printed or shown but not durably logged by themselves:

- `radio_transcribe.py` prints JSON/text to stdout; another process has to capture it.
- `radio_rds.py` prints JSON RDS messages to stdout; it does not persist them.
- `radio_scan.py` prints scan scores to stdout only.
- `radio_wfm.py` prints a startup line and then just plays audio.
- the monitor curses pane shows live transcript state, but the monitor still does not emit a transcript sidecar file of its own.

## Verified Behaviors In This Snapshot

Verified during the work that produced this bundle:

- OpenAI live transcription works on NOAA weather audio.
- `--all-local` resolves to a real local ASR backend on this machine instead of merely gesturing at one.
- Decoder raw output is logged persistently.
- Decoder likely-hit output is split into its own log.
- Archive jobs write rolling FLAC segments.
- Archive jobs flush transcript sidecars correctly on shutdown.
- Multichannel jobs write per-channel audio segments and transcript sidecars.
- Session layer blocks obvious collisions between `gqrx` and multichannel ownership.
- `radio-cortex` tails transcript logs and emits structured event records with the local Llama model.
- `radio-cortex` persists expectation/working-memory state across restarts.
- novelty suppression distinguishes repeated forecast loops from changed advisory content.
- discrete FM ad/event text can now carry structured fields like entity, location, date, and event type.
- the monitor/transcriber capture path can lock onto the `GQRX` sink-input instead of the full desktop mix.
- the slower inference pass can be triggered manually from the monitor and now exposes backend/model/mode in status.

Example cortex interpretation from smoke test:

- transcript:
  - `The forecast for North Texas tonight is clear and mild with south winds around 5 miles an hour.`
- event:
  - `type=weather`
  - `confidence=0.9`
  - `summary=clear and mild with south winds around 5 miles an hour`

## Known Rough Edges

- `gqrx` remote socket can still fail to come back after direct SDR jobs.
- Multichannel backend is still closer to “working operator tool” than finished operator console.
- There is no saved station-profile system yet.
- There is no full multichannel TUI yet.
- Cortex currently watches archive and multichannel transcript files, not the single-station monitor pane directly.
- Local `llama-cli` is usable, but not elegant; deterministic fallback narration is there for a reason.
- FM/event extraction is present, but entity/date fields still depend on transcript quality and the local model not wandering off into the weeds.
- Transcript quality still depends heavily on RF quality, demod mode, and whether the audio actually contains speech.

## Recommended Next Steps

If continuing this stack, the highest-value next improvements are:

1. Add event emitters directly in archive and multichannel backends for non-LLM heuristics like silence gaps, signal anomalies, and decoder bursts.
2. Feed the single-station monitor path into the same transcript/event stream as archive and multichannel.
3. Add saved station-set profiles for `radio multichannel start <profile>`.
4. Add VAD/chunk gating before remote transcription to reduce noise and cost.
5. Add replay tooling that reconstructs a time window across audio, transcripts, and events.
6. Finish the NeMo runtime install if local noisy-speech ASR matters more than API convenience.

## Bundle Intent

This zip is a reproducible code snapshot of the current radio tooling, not a perfectly sealed release.

If future-you opens this at 3am, the important truth is:

- single-station listening exists
- single-source archiving exists
- nearby-station simultaneous recording/playback exists
- shared session ownership exists
- local transcript interpretation exists
- far-apart frequencies still need another RF path

That is the actual state of the machine, minus the comforting lies.
