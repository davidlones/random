# Shared Dictionary

A small CLI that layers personal and shared meanings on top of canonical
Wiktionary definitions. It is intentionally non-destructive: canonical
meanings are cached, and personal overlays live in separate `dict_*.json`
files.

## What it does
- Looks up a word on Wiktionary (via REST API) and caches the result.
- Displays a ranked, deduped list of canonical definitions.
- Shows additional meanings from local overlay dictionaries.
- Lets you add your own definition to a chosen `dict_*.json` file.

## Requirements
- Python 3
- `requests`
- Optional: `termcolor` for colored output

Install dependencies (if missing):

```bash
python3 -m pip install --user requests termcolor
```

## Quick start

```bash
python3 shared_dictionary.py
```

Then type a word (e.g., `signal`) and press Enter.

## Commands
- `word`            Look up a word
- `:add`            Add your own definition to a `dict_*.json` file
- `:list`           List words with overlays
- `:search <text>`  Search overlay text
- `:help`           Show help
- `:quit` / `:q` / `0`  Exit

## Data files
- `dict_*.json`
  - Overlay dictionaries. Each file contains `user_id` and `entries`.
  - Example filename: `dict_sol.json`
- `canonical_cache.json`
  - Cached Wiktionary responses (per word).
- `.last_dict_user`
  - Remembers which `dict_*.json` file you last used for `:add`.

## Notes and behavior
- Canonical definitions are fetched from Wiktionary and cached locally.
- Sense ranking favors the first etymology and common parts of speech,
  while down-weighting niche senses.
- Overlay meanings are displayed newest-first (based on `created` time).
- If `termcolor` is missing, output falls back to plain text.

## Troubleshooting
- If canonical lookups fail, you may see a lookup error in the output.
  Check network access and try again.
- If you want to refresh canonical data, delete `canonical_cache.json`.

## Example overlay entry format

```json
{
  "user_id": "sol",
  "entries": {
    "signal": [
      {
        "text": "A shared mental ping meaning: I’m ready to talk.",
        "tags": [],
        "created": "2026-02-10T00:00:00Z"
      }
    ]
  }
}
```
