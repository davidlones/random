#!/usr/bin/env bash
# usage.sh — calm system pulse with optional active probes
# Dependencies:
#   coreutils, procps
#   optional: speedtest-cli or speedtest (Ookla)

###############################################################################
# Safety & intent
###############################################################################
# Observational by default.
# Active probes (network rate, speed test) are best-effort and non-fatal.

set -u
set -o pipefail

###############################################################################
# Formatting helpers
###############################################################################
BOLD="\033[1m"
DIM="\033[2m"
RESET="\033[0m"

cols() { tput cols 2>/dev/null || echo 80; }

hr() {
  printf "${DIM}%*s${RESET}\n" "$(cols)" "" | tr ' ' '-'
}

section() {
  echo
  echo -e "${BOLD}$1${RESET}"
  hr
}

###############################################################################
# Header
###############################################################################
echo -e "${BOLD}SYSTEM PULSE${RESET} — $(hostname) — $(date -Is)"
hr

###############################################################################
# Uptime & Load
###############################################################################
section "Uptime & Load"

uptime_pretty=$(uptime -p | sed 's/^up //')
read -r load1 load5 load15 _ < <(awk '{print $1, $2, $3}' /proc/loadavg)
cores=$(nproc)

printf "  Uptime: %s\n" "$uptime_pretty"
printf "  Load:   %.2f %.2f %.2f (cores: %d)\n" \
  "$load1" "$load5" "$load15" "$cores"

###############################################################################
# Memory
###############################################################################
section "Memory"

free -h | sed 's/^/  /'

avail_pct=$(free | awk '/Mem:/ {printf "%.0f", ($7/$2)*100}')
if (( avail_pct < 25 )); then
  echo
  echo -e "  ${BOLD}⚠ memory pressure:${RESET} ${avail_pct}% available"
fi

###############################################################################
# Disk Usage
###############################################################################
section "Disk Usage"

df -h -x tmpfs -x devtmpfs | sed 's/^/  /'

root_use=$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')
if (( root_use >= 90 )); then
  echo
  echo -e "  ${BOLD}⚠ disk pressure:${RESET} / is ${root_use}% full"
  echo "  Largest directories under / (depth 1):"
  ( du -h -x -d1 / 2>/dev/null || true ) \
    | sort -hr | head -n 6 | sed 's/^/    /'
fi

###############################################################################
# Network Activity (rate-based, averaged)
###############################################################################
section "Network Activity"

interval=3

read rx1 tx1 < <(
  awk '/:/ {rx+=$2; tx+=$10} END {print rx, tx}' /proc/net/dev
)

sleep "$interval"

read rx2 tx2 < <(
  awk '/:/ {rx+=$2; tx+=$10} END {print rx, tx}' /proc/net/dev
)

awk -v rx1="$rx1" -v rx2="$rx2" \
    -v tx1="$tx1" -v tx2="$tx2" \
    -v t="$interval" '
  BEGIN {
    if (rx2 == rx1 && tx2 == tx1) {
      print "  Network idle during sampling window"
    } else {
      printf "  RX rate: %.2f MB/s\n", (rx2 - rx1) / 1024 / 1024 / t
      printf "  TX rate: %.2f MB/s\n", (tx2 - tx1) / 1024 / 1024 / t
    }
  }
'

###############################################################################
# Active Network Probe (Speed Test)
###############################################################################
section "Network Speed Test (active probe)"

if command -v speedtest >/dev/null 2>&1; then
  # Ookla speedtest
  speedtest --simple 2>/dev/null || echo "  Speed test failed (non-fatal)"
elif command -v speedtest-cli >/dev/null 2>&1; then
  # Python speedtest-cli
  speedtest-cli --simple 2>/dev/null || echo "  Speed test failed (non-fatal)"
else
  echo "  Speed test tool not installed"
  echo "  (install: speedtest or speedtest-cli)"
fi

###############################################################################
# Process pressure
###############################################################################
section "Top CPU Consumers"
ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -n 6 | sed 's/^/  /'

section "Top Memory Consumers"
ps -eo pid,comm,%mem,%cpu --sort=-%mem | head -n 6 | sed 's/^/  /'

###############################################################################
# Footer
###############################################################################
hr
echo -e "${DIM}End of pulse.${RESET}"
exit 0
