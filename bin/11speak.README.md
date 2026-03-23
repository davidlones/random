# 11speak (`11speak.py`)

ElevenLabs text-to-speech CLI for local playback with:

- long-text chunking
- interactive dialogue mode (persistent process / lower turn latency)
- interruptible playback (`Ctrl-C`)
- stream fanout (speakers + file + tee command)

It started as a tiny one-shot script and now behaves more like a usable TTS terminal tool.

## What It Does

- Sends text to ElevenLabs TTS and streams audio back in real time.
- Plays audio locally through `ffplay`.
- Splits long text into chunked requests so longer context strings do not require a single giant API request.
- Supports continuous dialogue mode so you can keep the process and player alive across turns.
- Can duplicate the raw audio stream to:
  - local speakers
  - a file
  - another process (visualizer/recorder/etc.)

## Requirements

- Python 3.10+
- `ffplay` (usually from `ffmpeg`)
- ElevenLabs Python SDK (`elevenlabs`)
- `ELEVENLABS_API_KEY` environment variable

Example install:

```bash
python3 -m pip install --user elevenlabs
sudo apt install ffmpeg
export ELEVENLABS_API_KEY=YOUR_API_KEY_HERE
```

## Quick Start

One-shot speech:

```bash
python3 random/bin/11speak.py "Hello from 11speak."
```

Pipe text in (recommended for long content):

```bash
cat notes.txt | python3 random/bin/11speak.py -
```

Read from file:

```bash
python3 random/bin/11speak.py --file notes.txt
```

## Key Features

### 1) Long Context Support (Chunking)

Long text is split into smaller chunks (sentence-aware first, then word-split fallback). This avoids giant single requests and makes long narrations practical.

- Default chunk size: `700` chars
- Tune with: `--chunk-chars`

Dry-run chunking without API usage:

```bash
python3 random/bin/11speak.py --dry-run --chunk-chars 500 --file notes.txt
```

### 2) Dialogue Mode (Persistent Process)

`--dialogue` keeps the script running and reuses the ElevenLabs client plus local playback process (`ffplay`).

Why this matters:
- lower per-turn overhead
- better for live interaction / continuous dialogue
- easier to interrupt and continue

Start dialogue mode:

```bash
python3 random/bin/11speak.py --dialogue
```

Dialogue commands:
- `/stop` : restart the local player (manual reset)
- `/quit` or `/exit` : end the session

Interrupt key:
- `Ctrl-C` during playback interrupts the current utterance and restarts the player
- the dialogue session stays alive

### 3) Stream Fanout (Tee)

The tool can write the same streamed audio bytes to multiple outputs at once:

- local speakers (default)
- `--save-stream <file>`
- `--tee-cmd "<command>"`

Example (speakers + file):

```bash
python3 random/bin/11speak.py --dialogue --save-stream session.mp3
```

Example (speakers + file + tee process):

```bash
python3 random/bin/11speak.py --dialogue \
  --save-stream session.mp3 \
  --tee-cmd "dd of=/tmp/tts-stream.mp3 status=none"
```

Silent fanout test (no speaker playback):

```bash
python3 random/bin/11speak.py --dialogue --no-speaker \
  --save-stream session.mp3 \
  --tee-cmd "dd of=/tmp/tts-stream.mp3 status=none"
```

Important gotcha:
- `--tee-cmd` is executed directly (no shell).
- Shell redirection like `> out.mp3` will fail unless you explicitly invoke a shell.
- Use commands that read from stdin directly (`dd`, `ffmpeg`, a visualizer binary, etc.).

## CLI Reference

```text
usage: 11speak.py [-h] [--file FILE] [--chunk-chars CHUNK_CHARS]
                  [--voice-id VOICE_ID] [--model-id MODEL_ID] [--dry-run]
                  [--dialogue] [--save-stream SAVE_STREAM] [--tee-cmd TEE_CMD]
                  [--no-speaker]
                  [text ...]
```

Arguments:

- `text ...`
  - Text to speak.
  - Use `-` (or omit and pipe stdin) to read from stdin.

Options:

- `--file FILE`
  - Read text from a file.

- `--chunk-chars N`
  - Chunk long input into requests of roughly `N` characters.
  - Minimum allowed: `50`.

- `--voice-id ID`
  - Override the ElevenLabs voice ID.

- `--model-id ID`
  - Override the ElevenLabs model ID.

- `--dry-run`
  - Parse input and show chunking info only.
  - No API call, no audio playback.

- `--dialogue`
  - Keep process and player open for multiple turns.

- `--save-stream PATH`
  - Save raw streamed audio bytes (currently MP3 stream) to file.

- `--tee-cmd "CMD ..."`
  - Pipe the same raw stream to another command.

- `--no-speaker`
  - Disable local playback and only use file/tee outputs.

## Behavior Notes

- Current stream format is MP3 (`mp3_44100_128`).
- Chunking is text-level; audio is streamed sequentially across chunks.
- In one-shot mode, shutdown is graceful so `ffplay` can drain buffered audio.
- In dialogue mode, interrupts (`Ctrl-C`) are forceful by design so speech stops immediately.

## Latency Notes

The current latency wins already come from:

- reusing the Python process
- reusing the ElevenLabs client
- reusing `ffplay` in dialogue mode
- avoiding giant one-shot requests with chunking

Further improvement path (future):

- switch output to PCM instead of MP3 for lower decode latency and easier visualizer integration
- adapt tee/visualizer consumers to expect PCM
- potentially move to a dedicated low-latency audio output path instead of `ffplay`

## Troubleshooting

### Quota / Limit Errors

If you see a message like:

```text
ElevenLabs quota exceeded: ...
```

That may be:
- a per-key/project limit
- a different key/workspace than the one shown in your dashboard
- model/voice/output settings causing higher-than-expected cost

The script now prints a concise error message instead of a Python traceback.

### I Only Hear Part of the First Word

This previously happened when playback was being terminated too aggressively at session shutdown (especially in piped dialogue tests with immediate `/quit`).

Current behavior:
- one-shot and normal dialogue shutdown wait for `ffplay` to drain buffered audio
- `Ctrl-C` and `/stop` still cut immediately (intended)

### No Sound, But Files Are Written

Common causes:
- you used `--no-speaker`
- audio sink / PulseAudio / PipeWire issue on the machine
- `ffplay` missing or broken

Quick checks:

```bash
command -v ffplay
python3 random/bin/11speak.py "test"
```

### `--tee-cmd` Fails With `>` Errors

That means shell syntax was passed directly as args. Example of what not to do:

```bash
--tee-cmd "cat > out.mp3"
```

Use a real stdin-consuming command instead:

```bash
--tee-cmd "dd of=out.mp3 status=none"
```

## Examples

Narrate a long system summary from a file:

```bash
python3 random/bin/11speak.py --file system_summary.txt --chunk-chars 600
```

Interactive dialogue with logging to an MP3:

```bash
python3 random/bin/11speak.py --dialogue --save-stream dialogue_session.mp3
```

Chunking preview for a huge prompt:

```bash
python3 random/bin/11speak.py --dry-run --chunk-chars 350 --file huge_prompt.txt
```

## Implementation Notes (for future-you)

- The audio fanout lives in an `AudioFanout` class and handles speaker/file/tee sinks.
- Dialogue mode is line-oriented and intentionally simple (`input()` / stdin readline).
- `/stop` restarts only the local player; it does not cancel an in-flight API request already completed.
- `Ctrl-C` during playback interrupts the active utterance by restarting the player.

## Future Upgrades (Obvious Next Steps)

- PCM output mode (`--pcm`) for low-latency + visualizers
- explicit `--tee-shell` (opt-in shell execution) for pipeline/redirection convenience
- session log mode (timestamp, chars, chunks, duration, error/success)
- queue mode / async producer-consumer for overlapping generation and playback
- microphone input / STT loop if you want full voice dialogue plumbing
