#!/bin/bash

CACHE=/run/motd.codex
TTL=300   # seconds

generate() {
  timeout 20 codex exec --ephemeral "summarize the system in 5 concise bullet points" --dangerously-bypass-approvals-and-sandbox \
    | sed 's/^/  /' \
    > "$CACHE.tmp" 2>/dev/null

  [ -s "$CACHE.tmp" ] && mv "$CACHE.tmp" "$CACHE"
}

# refresh if missing or stale
if [ ! -f "$CACHE" ] || [ $(($(date +%s) - $(stat -c %Y "$CACHE"))) -gt $TTL ]; then
  generate &
fi

# print immediately
if [ -f "$CACHE" ]; then
  echo
  echo "SOL NODE — CODEX SUMMARY"
  cat "$CACHE"
fi

