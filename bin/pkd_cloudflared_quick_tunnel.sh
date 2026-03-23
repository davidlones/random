#!/usr/bin/env bash
set -euo pipefail

origin_url="${1:-http://127.0.0.1:8888}"
log_dir="/home/david/random/logs"
log_file="${log_dir}/pkd_cloudflared_quick_tunnel.log"
pid_file="${log_dir}/pkd_cloudflared_quick_tunnel.pid"
cloudflared_bin="${HOME}/.local/bin/cloudflared"
url_pattern='https://[-[:alnum:]]+\.trycloudflare\.com'

mkdir -p "${log_dir}"

if [[ ! -x "${cloudflared_bin}" ]]; then
  echo "cloudflared not found at ${cloudflared_bin}" >&2
  echo "Install it first, then rerun this script." >&2
  exit 1
fi

if [[ -f "${pid_file}" ]]; then
  existing_pid="$(cat "${pid_file}")"
  if [[ -n "${existing_pid}" ]] && kill -0 "${existing_pid}" 2>/dev/null; then
    echo "cloudflared quick tunnel already running with PID ${existing_pid}" >&2
    echo "Log: ${log_file}" >&2
    exit 0
  fi
  rm -f "${pid_file}"
fi

: > "${log_file}"
nohup "${cloudflared_bin}" tunnel --no-autoupdate --logfile "${log_file}" --url "${origin_url}" >/dev/null 2>&1 &
pid=$!
echo "${pid}" > "${pid_file}"

public_url=""
for _ in $(seq 1 20); do
  if ! kill -0 "${pid}" 2>/dev/null; then
    echo "cloudflared exited early; inspect ${log_file}" >&2
    exit 1
  fi
  public_url="$(rg -o -m 1 "${url_pattern}" "${log_file}" || true)"
  if [[ -n "${public_url}" ]]; then
    break
  fi
  sleep 1
done

echo "Started cloudflared quick tunnel for ${origin_url}"
echo "PID: ${pid}"
echo "Log: ${log_file}"
if [[ -n "${public_url}" ]]; then
  echo "Public URL: ${public_url}"
else
  echo "Public URL not found yet. Check later with:"
  echo "  rg -o '${url_pattern}' ${log_file}"
fi
