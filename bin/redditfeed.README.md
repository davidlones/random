# redditfeed (`redditfeed.py`)

`redditfeed.py` is a lightweight Reddit reader with:

- terminal output for quick scanning
- optional comment tree printing
- JSON output for piping into other tools
- a Tk GUI for scrolling posts, images, and comments
- local caching for Reddit JSON and post images

- Script: `/home/david/random/bin/redditfeed.py`
- Cache root: `~/.cache/sol_reddit`

## What It Does

- Fetches hot posts from a subreddit or Reddit front page (`all` by default).
- Prints a readable CLI feed with timestamps, author, score, body preview, and permalink.
- Prints full nested comments for one selected post with `--comments`.
- Emits raw JSON post data with `--json`.
- Launches a scrollable desktop GUI with inline images, keyboard navigation, and comment toggling.

## Requirements

- Python 3
- `requests`
- `Pillow`
- Tkinter available in the local Python install
- working network access to `reddit.com`

Example install:

```bash
python3 -m pip install --user requests pillow
```

## Quick Start

Front page in the terminal:

```bash
python3 /home/david/random/bin/redditfeed.py
```

Specific subreddit:

```bash
python3 /home/david/random/bin/redditfeed.py linux
```

Launch GUI:

```bash
python3 /home/david/random/bin/redditfeed.py --gui
```

## CLI Usage

```text
usage: redditfeed.py [-h] [--gui] [--limit LIMIT] [--comments COMMENTS] [--json] [subreddit]
```

Arguments:

- `subreddit`
  - Optional subreddit name.
  - Defaults to `all`.
  - `r/linux` is accepted in the GUI input; CLI is happiest with plain names like `linux`.

Options:

- `--gui`
  - Launch the Tk desktop interface instead of terminal output.

- `--limit N`
  - Number of posts to fetch in CLI mode.
  - Default: `20`.

- `--comments N`
  - Print nested comments for CLI post number `N`.

- `--json`
  - Print fetched post objects as JSON instead of human-readable text.

## Examples

Show 5 posts from `technology`:

```bash
python3 /home/david/random/bin/redditfeed.py technology --limit 5
```

Print comments for the second post:

```bash
python3 /home/david/random/bin/redditfeed.py technology --limit 5 --comments 2
```

Pipe post data to `jq`:

```bash
python3 /home/david/random/bin/redditfeed.py technology --limit 3 --json | jq '.[].title'
```

Read the front page in text-mode browser style:

```bash
python3 /home/david/random/bin/redditfeed.py linux | w3m -T text/html
```

## GUI Behavior

The GUI opens a dark-themed feed window and supports:

- subreddit entry + `Load`
- `Front Page`
- `Reload`
- inline post images when previews are available
- `show comments` / `hide comments`
- `open in browser`
- infinite-ish loading as you scroll near the bottom

Keyboard shortcuts:

- `j` move selection down
- `k` move selection up
- `o` open selected post in browser
- `r` reload current subreddit
- `Enter` toggle comments on selected post

## Cache Behavior

Cache directories:

- JSON: `~/.cache/sol_reddit/json`
- images: `~/.cache/sol_reddit/image`

Current behavior:

- Reddit JSON responses are cached for `300` seconds.
- If a fresh fetch fails but cached JSON exists, the cached copy is reused.
- Downloaded images are cached by URL hash and reused on later views.

## Output Notes

- CLI output shows local timestamps in `[M/D/YYYY H:MM:SS AM/PM]` format.
- Body text in CLI mode is shortened for scanning.
- Comment output is printed as a nested text tree with author and score.

## Caveats

- This reads `hot.json`, not `new`, `top`, or search results.
- Reddit rate-limiting, API changes, or anti-bot behavior can still ruin the party.
- GUI mode depends on a working desktop session; headless shells will not love it.
- Some image URLs may fail or preview data may be missing; the post still renders.

## Troubleshooting

Import errors:

```bash
python3 -m pip install --user requests pillow
```

Quick syntax/help check:

```bash
python3 /home/david/random/bin/redditfeed.py --help
```

Clear cached data:

```bash
rm -rf ~/.cache/sol_reddit
```

That last one is destructive, obviously, but also harmless enough unless you were emotionally attached to cached thumbnails.
