# Sol-37 Site Audit

Date: 2026-03-23
Repo: `/home/david/random`
Site root: `/home/david/random/www`
Primary host: `sol.system42.one`
Local origin: `http://127.0.0.1:8888`

## Purpose

Sol-37 is a static-site archive presented as a Windows 95-style operating environment. The site is not a framework app. It is a set of static HTML, JSON, media, and helper scripts served by Caddy, with local backend services layered in for chat/logbook and metrics.

This document is the committed, auditable summary of the site as inspected on 2026-03-23. It is meant to be read alongside the local skill `sol37-site-audit` in `~/.codex/skills`.

## Git History

The committed history for the site is short.

1. `5da6435` - import Sol-37 site and support scripts.
2. `e1444fe` - refine site metrics monitor and add `robots.txt`.

That means most of the narrative complexity comes from current filesystem state and running services, not a long sequence of commits.

## Current Tree Shape

The public web root mixes several content modes:

- `index.html`: main retro desktop shell.
- `sitemap.html`: Explorer-style file browser for the public tree.
- `site-metrics.html` + `site-metrics.json`: traffic monitor UI and generated data.
- `posts/`: Markdown and HTML post content.
- `programs/`: app-like pages embedded by the shell.
- top-level `.html` files: archive documents and standalone pages.
- `video/`: media-player playlist and video assets.
- `sol37/`: older alternate shell/content variant retained in-tree.

Representative files:

- `www/index.html`
- `www/sitemap.html`
- `www/site-metrics.html`
- `www/programs/logbook.html`
- `www/programs/star-map.html`
- `www/programs/media-player.html`
- `www/posts/index.json`
- `www/video/playlist.json`

## Frontend Architecture

### Main Shell

`www/index.html` is the primary shell and landing page. It implements:

- draggable Win95 windows
- taskbar state
- Start menu
- terminal-like command input
- iframe-backed program windows
- desktop icons for programs and archive entry points
- blog/document opening behavior

The shell is a large single-file app. That keeps deployment simple, but it also makes maintenance expensive because interaction logic, UI, navigation, and program registration all live in one document.

### Programs

Current program surfaces:

- `sitemap.html`: file-browser style archive navigator
- `programs/star-map.html`: interactive star map program
- `programs/logbook.html`: IRC-backed public logbook client
- `programs/media-player.html`: local media player with playlist/fallback logic

### Blog / Archive

Posts are listed through `posts/index.json`. The shell can open either Markdown-backed or HTML-backed entries, while many archive pages remain directly reachable at top-level URLs.

## Backend Architecture

### Caddy

`bin/Caddyfile.pkd_share` serves `www/` on `:8888`, compresses responses, caches static media, and reverse-proxies `/api/logbook/*` to the local logbook API at `127.0.0.1:8890`.

### Cloudflare Tunnel

The site is published through Cloudflare tunnel infrastructure. The repo includes a quick-tunnel helper in `bin/pkd_cloudflared_quick_tunnel.sh`, while the current host is using a named Cloudflare tunnel for `sol.system42.one`.

### IRC / Logbook Stack

The public logbook is backed by local IRC infrastructure:

- `ngircd` listens on `6667`
- `bin/public_logbook_api.py` exposes `GET/POST /messages`
- `bin/public_logbook_irc_logger.py` joins allowed channels and persists chat to JSONL storage
- `bin/logbook_irc_common.py` centralizes validation, deduplication, storage, and IRC send logic

Allowed channels currently include:

- `public-logbook`
- `archive-watch`
- `civilization-sim`

### Synthetic Log Layer

The logbook view is not purely organic traffic. `bin/synthetic_logbook_snapshot.py` and `data/logbook/synthetic_config.json` generate seeded/transient messages that are merged with real log data. That is part of the product behavior and should be treated as intentional site fiction/atmosphere, not an accidental artifact.

### Metrics Pipeline

`bin/site_metrics_snapshot.py` parses Caddy access logs from `/tmp/pkd_caddy_access.log` and writes `www/site-metrics.json`, which is then rendered by `www/site-metrics.html`.

### Video Pipeline

`bin/video_playlist_watch.py` watches `www/video/`, converts unsupported formats to browser-safe H.264 derivatives, and rebuilds `www/video/playlist.json`.

This supports the media-player program and is the main recent local extension that has not yet been fully reflected in historical commits.

## Running Services Observed On 2026-03-23

Observed active and functioning:

- Caddy serving `127.0.0.1:8888`
- Cloudflare tunnel process for public publishing
- `ngircd`
- `public-logbook-api.service`
- `public-logbook-irc-logger.service`

Functional checks passed:

- `GET /` on `127.0.0.1:8888`
- direct logbook API fetch from `127.0.0.1:8890/messages`
- Caddy reverse-proxy path to `/api/logbook/messages`

Known wrinkle:

- the Python logbook API responds poorly to `HEAD` and currently behaves like a minimal custom handler rather than a fully polished HTTP service
- Cloudflare tunnel logs show intermittent resolver / QUIC noise, but the tunnel also successfully re-registers

## Recent Live Changes Not Originally In Git History

Recent local work added or refined:

- media player registration in the shell
- `programs/media-player.html`
- `video/playlist.json`
- `video/` source files and H.264 fallback derivative
- `bin/video_playlist_watch.py`
- window-opening behavior improvements in `index.html` so windows open foregrounded rather than occasionally minimized

These changes are real and operational, but part of this audit effort is to ensure they are actually committed and no longer live only in local state.

## Strengths

- strong cohesive identity
- static deployment model with low serving complexity
- desktop-shell metaphor is implemented consistently enough to feel deliberate
- archive, tooling, and backend are integrated into one navigable experience
- local-first services make iteration fast

## Weaknesses

- source of truth is split across git, filesystem state, and long-running daemons
- `index.html` is large enough to become a maintenance trap
- some features have existed in sessions/runtime before being committed
- service definitions live partly outside the repo, limiting reproducibility
- the line between real and synthetic logbook content is product-significant and easy to forget

## Audit Commands

Useful commands for future verification:

```bash
git -C /home/david/random status --short
git -C /home/david/random log --oneline --decorate -- www bin
find /home/david/random/www -maxdepth 2 -type f | sort
curl -I -s http://127.0.0.1:8888/
curl -s 'http://127.0.0.1:8890/messages?channel=public-logbook&limit=3'
ps -ef | rg -i 'caddy|cloudflared|ngircd|public_logbook|irc'
ss -ltnp | rg '(:8888|:6667|:8890)'
systemctl --user --type=service --state=running | rg -i 'cloudflared|logbook'
```

## Recommended Discipline

- keep audit summaries like this in-repo, not only in session logs
- commit site features together with the files that operationalize them
- avoid expanding `index.html` further without considering a light modular split
- treat synthetic logbook content as a documented system layer
- keep service assumptions explicit whenever the shell claims runtime-backed features
