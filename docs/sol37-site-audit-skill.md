# Local Skill Mirror: sol37-site-audit

This file mirrors the local skill installed at:

- `/home/david/.codex/skills/sol37-site-audit/SKILL.md`

It exists so the skill itself is auditable in the `random` repo even though `~/.codex` is not currently a git repository.

## Frontmatter

```yaml
name: sol37-site-audit
description: Use when working on, reviewing, auditing, documenting, or verifying the Sol-37 site in `random/www`, including its retro shell, program pages, content tree, git history, local daemons, Caddy/Cloudflare publishing, logbook backend, metrics pipeline, and media-player/video ingestion path.
```

## Body

```md
# Sol37 Site Audit

Use this skill for requests about the site in `/home/david/random/www` and its supporting services.

## Scope

This skill covers:

- site reviews and architectural summaries
- git-history inspection for `random/www` and related backend scripts
- runtime verification of Caddy, Cloudflare tunnel, IRC/logbook, and metrics services
- content/tree mapping for the public web root
- media-player and `/video` pipeline review

## Canonical Audit Summary

The committed audit summary lives at:

- `/home/david/random/docs/sol37-site-audit.md`

Read that first for current architecture, history, strengths, weaknesses, and verification commands.

## Files To Inspect First

- `/home/david/random/www/index.html`
- `/home/david/random/www/sitemap.html`
- `/home/david/random/www/site-metrics.html`
- `/home/david/random/www/programs/logbook.html`
- `/home/david/random/www/programs/star-map.html`
- `/home/david/random/www/programs/media-player.html`
- `/home/david/random/bin/Caddyfile.pkd_share`
- `/home/david/random/bin/public_logbook_api.py`
- `/home/david/random/bin/public_logbook_irc_logger.py`
- `/home/david/random/bin/site_metrics_snapshot.py`
- `/home/david/random/bin/video_playlist_watch.py`

## Standard Audit Workflow

1. Inspect repo state:
   `git -C /home/david/random status --short`
2. Inspect recent site/backend history:
   `git -C /home/david/random log --oneline --decorate -- www bin`
3. Map the current public tree:
   `find /home/david/random/www -maxdepth 2 -type f | sort`
4. Verify the local web origin:
   `curl -I -s http://127.0.0.1:8888/`
5. Verify runtime daemons when relevant:
   `ps -ef | rg -i 'caddy|cloudflared|ngircd|public_logbook|irc'`
6. Verify ports when relevant:
   `ss -ltnp | rg '(:8888|:6667|:8890)'`
7. Verify logbook API when relevant:
   `curl -s 'http://127.0.0.1:8890/messages?channel=public-logbook&limit=3'`

## Review Priorities

Prioritize:

- what is committed vs only live locally
- what the shell claims exists vs what the machine actually runs
- backend dependencies that are outside git
- archive/content discoverability
- program-window integration in `index.html`
- media-player and `/video` pipeline behavior

## Notes

- Treat Sol-37 as a static-site shell with runtime-backed subsystems, not as a framework app.
- Preserve the distinction between committed repo history and current machine state.
- When producing summaries, call out uncommitted operational features explicitly.
```
