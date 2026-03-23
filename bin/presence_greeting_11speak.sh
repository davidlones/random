#!/bin/bash

set -euo pipefail

CACHE_DIR="$HOME/.cache/morning_greeting_11speak"
LOG_FILE="$HOME/logs/presence_greeting_11speak.log"
VOICE_TAG="default-voice"
: "${DAVID_IP:=10.0.1.146}"
: "${CLINT_IP:=10.0.1.185}"

log() {
  local level="$1"
  shift
  local line
  line="$(printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$level" "$*")"
  printf '%s\n' "$line" >> "$LOG_FILE"
  if [ -t 1 ]; then
    printf '%s\n' "$line"
  fi
}

presence_state="unknown"

# Load a dedicated env file first so cron gets stable credentials.
if [ -f "$HOME/.config/masterbot.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$HOME/.config/masterbot.env"
  set +a
fi

# Cron does not carry your interactive shell environment, so load the usual
# shell startup files as a secondary fallback.
if [ -f "$HOME/.profile" ]; then
  # shellcheck disable=SC1090
  . "$HOME/.profile"
fi

if [ -f "$HOME/.bashrc" ]; then
  # shellcheck disable=SC1090
  . "$HOME/.bashrc"
fi

mkdir -p "$CACHE_DIR" "$(dirname "$LOG_FILE")"

time_override="${1:-}"
if [ -n "$time_override" ]; then
  base_time="$(date -d "$time_override" '+%Y-%m-%d %H:%M')"
  hour_24="$(date -d "$base_time" '+%H')"
  minute_text="$(date -d "$base_time" '+%M')"
  if [ "$minute_text" = "00" ]; then
    time_text="$(date -d "$base_time" '+%-I %p')"
  else
    time_text="$(date -d "$base_time" '+%-I:%M %p')"
  fi
else
  base_time="$(date '+%Y-%m-%d %H:%M')"
  hour_24="$(date -d "$base_time" '+%H')"
  minute_text="$(date -d "$base_time" '+%M')"
  if [ "$minute_text" = "00" ]; then
    time_text="$(date -d "$base_time" '+%-I %p')"
  else
    time_text="$(date -d "$base_time" '+%-I:%M %p')"
  fi
fi

if [ "$hour_24" -ge 5 ] && [ "$hour_24" -lt 12 ]; then
  salutation="Good morning"
elif [ "$hour_24" -ge 12 ] && [ "$hour_24" -lt 17 ]; then
  salutation="Good afternoon"
elif [ "$hour_24" -ge 17 ] && [ "$hour_24" -lt 22 ]; then
  salutation="Good evening"
else
  salutation="Good night"
fi

play_cached_mp3() {
  local mp3_path="$1"
  ffmpeg -nostdin -loglevel error -i "$mp3_path" -f wav - 2>/dev/null | paplay
}

is_present_by_ip() {
  local ip_addr="$1"
  if ping -c 1 -W 1 "$ip_addr" >/dev/null 2>&1; then
    presence_state="ping"
    return 0
  fi

  if ip neigh show "$ip_addr" 2>/dev/null | grep -Eq 'REACHABLE|STALE|DELAY|PROBE'; then
    presence_state="neighbor"
    return 0
  fi

  presence_state="absent"
  return 1
}

present_names=()
if is_present_by_ip "$DAVID_IP"; then
  present_names+=("David")
  log "PRESENCE" "David appears present via $DAVID_IP using $presence_state. Apparently he does, in fact, exist on the LAN."
else
  log "PRESENCE" "David missing at $DAVID_IP. Either he is away or the network is being cagey again."
fi

if is_present_by_ip "$CLINT_IP"; then
  present_names+=("Clint")
  log "PRESENCE" "Clint appears present via $CLINT_IP using $presence_state. The router condescends to admit it."
else
  log "PRESENCE" "Clint absent at $CLINT_IP. No convincing sign of life from that address."
fi

if [ "${#present_names[@]}" -eq 2 ]; then
  audience_text="David and Clint"
elif [ "${#present_names[@]}" -eq 1 ]; then
  audience_text="${present_names[0]}"
else
  audience_text=""
fi

if [ -n "$audience_text" ]; then
  greeting_text="${salutation}, ${audience_text}. The time is ${time_text}."
else
  greeting_text="${salutation} to no one in particular. The time is ${time_text}."
fi

log "RUN" "Base time ${base_time}; spoken time '${time_text}'; audience '${audience_text:-nobody}'."
log "RUN" "Chosen greeting: ${greeting_text}"

cache_key="$(printf '%s\n%s\n' "$VOICE_TAG" "$greeting_text" | sha256sum | awk '{print $1}')"
cache_path="$CACHE_DIR/${cache_key}.mp3"
log "CACHE" "Cache key ${cache_key}; path ${cache_path}. Repetition has its uses."

chime_time="$(
  python3 - "$base_time" <<'PY'
import datetime as dt
import sys

moment = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d %H:%M")
best = None
for delta in (-15, 0, 15):
    base = moment + dt.timedelta(minutes=delta)
    for quarter in (0, 15, 30, 45):
        candidate = base.replace(minute=quarter, second=0, microsecond=0)
        diff = abs((candidate - moment).total_seconds())
        key = (diff, candidate)
        if best is None or key < best:
            best = key
print(best[1].strftime("%H:%M"))
PY
)"
log "CHIME" "Nearest Westminster target is ${chime_time}. Close enough for bell work."

if [ -n "${ELEVENLABS_API_KEY:-}" ]; then
  if [ -s "$cache_path" ]; then
    log "CACHE" "Cache hit. Reusing existing audio instead of paying ElevenLabs to rediscover the obvious."
    if ! play_cached_mp3 "$cache_path"; then
      log "WARN" "Cached greeting playback failed; continuing to chime because melodrama is optional."
    fi
  else
    log "CACHE" "Cache miss. Generating a fresh greeting clip."
    if ! /usr/bin/env python3 /home/david/random/bin/11speak.py \
      --save-stream "$cache_path" \
      "$greeting_text"; then
      rm -f "$cache_path"
      log "WARN" "11speak greeting failed; continuing to chime because the clock still knows its job."
    else
      log "CACHE" "Generated and cached new greeting audio."
    fi
  fi
else
  log "WARN" "ELEVENLABS_API_KEY not set; skipping spoken greeting and continuing to chime like a slightly offended tower."
fi

log "CHIME" "Starting Westminster chime for ${chime_time}."
exec /usr/bin/env python3 /home/david/random/bin/westminster_chime.py \
  --backend paplay \
  --time "$chime_time"
