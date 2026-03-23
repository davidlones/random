# random

`random` is a curated public snapshot of an active personal systems lab.

It contains a few distinct things living in the same tree:

- a live retro-styled website and archive shell
- Python and shell utilities for automation, experiments, and local tooling
- radio-monitoring / transcription / cortex-style interpretation code
- a public IRC-backed logbook with synthetic ambient messages configurable from JSON

Live site:

- https://sol.system42.one/

This is not a polished single-product repository. It is closer to a working notebook with executable parts.

## What is here

### `www/`

The current public site.

- retro desktop UI
- file-browser-style site map
- star map / data-visualization experiments
- public logbook client
- taskbar metrics sourced from local web logs

If you want the most immediately understandable part of the repo, start here.

### `bin/`

The tool shelf.

This directory mixes small utilities, one-off experiments, local service scripts, and a few more serious subsystems. Notable threads include:

- site support scripts such as `public_logbook_api.py`, `public_logbook_irc_logger.py`, `site_metrics_snapshot.py`, and `pkd_cloudflared_quick_tunnel.sh`
- radio stack tooling such as `radio`, `radio_session.py`, `radio_monitor.py`, `radio_transcribe.py`, and `radio_cortex.py`
- assistant / automation experiments including `masterbot_v3.py`, `solmail.py`, `sol_ingest.py`, `11speak.py`, and `custom_notify.py`

Some of these are useful. Some are artifacts of late-night systems enthusiasm. Read before running, obviously.

### `radio-cortex/`

Supporting material for the radio stack and interpretation layer. This is where the project leans hardest into "signals, events, and correlation" rather than plain text processing.

### `data/`

Runtime-adjacent project data.

One public-facing example is:

- `data/logbook/synthetic_config.json`

That file controls the synthetic ambient entries used by the site's public logbook.

### `transcripts/`

Archived text artifacts, notes, and transcript-like outputs from related tooling and experiments.

## What this repo is not

- not a clean package with one install command
- not a stable SDK
- not a promise that every script is portable or safe on another machine

Several scripts assume local services, local paths, local devices, or personal infrastructure. If a file name sounds like it might talk to hardware, the network, or a daemon, assume it probably does.

## Public snapshot caveat

This GitHub repository is a curated public snapshot of the current state, not the full private working history. Large binary baggage and some purely local artifacts were intentionally omitted before publishing.

That means:

- the public repo is representative, not exhaustive
- some scripts may reference local files or services that are not present here
- the repo history on GitHub is intentionally simpler than the private local history

## Good entry points

If you want to browse instead of spelunk blindly:

- `www/index.html`
- `www/sitemap.html`
- `www/programs/logbook.html`
- `bin/public_logbook_api.py`
- `bin/public_logbook_irc_logger.py`
- `bin/synthetic_logbook_snapshot.py`
- `bin/radio_session.py`
- `bin/radio_monitor.py`
- `bin/radio_cortex.py`
- `RADIO_STACK_README.md`
- `CODEX_48H_MAJOR_CHANGES.md`

## Safe-ish first look

Start with inspection, not execution:

```bash
git clone https://github.com/davidlones/random.git
cd random
find . -maxdepth 2 -type f | sort | less
sed -n '1,220p' README.md
sed -n '1,220p' RADIO_STACK_README.md
sed -n '1,220p' www/index.html
```

If you want to explore the site locally as static files, serving `www/` is the least surprising place to begin.

## Running anything

Use judgment.

- Read scripts before executing them.
- Expect hard-coded paths in places.
- Expect service assumptions in places.
- Expect some code to be experimental by design.

The repo is interesting because it is real and lived-in. That is also what makes it slightly feral.
