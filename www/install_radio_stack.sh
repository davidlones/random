#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
install_radio_stack.sh

Installs the current radio stack dependencies on Debian/Ubuntu-ish systems.

What it does:
  - installs required apt packages for the radio stack
  - installs Python user packages for scripts that run under /usr/bin/env python3
  - creates ~/.venvs/radio with system site packages for the multichannel backend
  - optionally creates ~/.venvs/radio-asr for NeMo
  - optionally installs llama-cpp-python for local in-process cortex inference
  - links ~/random/bin/radio into ~/.local/bin/radio when possible

Usage:
  bash install_radio_stack.sh [options]

Options:
  --with-nemo         Install the heavier NeMo ASR runtime into ~/.venvs/radio-asr
  --with-llama-cpp    Install llama-cpp-python into the user Python environment
  --with-caddy        Install caddy via apt
  --no-apt            Skip apt installs and only do Python/venv setup
  --bundle-dir DIR    Path containing bin/radio and radio-cortex/ (default: parent of script if valid)
  --help              Show this help text

Notes:
  - OpenAI transcription and the current default cortex backend both need OPENAI_API_KEY.
  - Cortex can still run locally via llama-cli / llama.cpp, but the bundled config currently defaults to OpenAI.
  - The current OpenAI cortex path expects a recent OpenAI Python SDK because classification now uses structured JSON outputs.
  - Local cortex still needs a GGUF model file if you switch back to a local backend.
  - NeMo is optional because it is large and mildly vindictive on disk usage.
EOF
}

WITH_NEMO=0
WITH_LLAMA_CPP=0
WITH_CADDY=0
NO_APT=0
BUNDLE_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-nemo)
      WITH_NEMO=1
      ;;
    --with-llama-cpp)
      WITH_LLAMA_CPP=1
      ;;
    --with-caddy)
      WITH_CADDY=1
      ;;
    --no-apt)
      NO_APT=1
      ;;
    --bundle-dir)
      shift
      BUNDLE_DIR="${1:-}"
      if [[ -z "${BUNDLE_DIR}" ]]; then
        echo "--bundle-dir requires a path" >&2
        exit 1
      fi
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${BUNDLE_DIR}" ]]; then
  CANDIDATE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
  if [[ -x "${CANDIDATE_DIR}/bin/radio" && -d "${CANDIDATE_DIR}/radio-cortex" ]]; then
    BUNDLE_DIR="${CANDIDATE_DIR}"
  fi
fi

RADIO_VENV="${HOME}/.venvs/radio"
RADIO_ASR_VENV="${HOME}/.venvs/radio-asr"
LOCAL_BIN="${HOME}/.local/bin"

APT_PACKAGES=(
  build-essential
  curl
  ffmpeg
  git
  gnuradio
  gr-osmosdr
  gqrx-sdr
  hackrf
  imagemagick
  multimon-ng
  pkg-config
  pulseaudio-utils
  python3
  python3-dev
  python3-pip
  python3-venv
  tesseract-ocr
  unzip
  xvfb
  xdotool
  zip
)

if [[ "${WITH_CADDY}" -eq 1 ]]; then
  APT_PACKAGES+=(caddy)
fi

USER_PIP_PACKAGES=(
  'openai>=2.29.0'
  PyYAML
  vosk
)

if [[ "${WITH_LLAMA_CPP}" -eq 1 ]]; then
  USER_PIP_PACKAGES+=(llama-cpp-python)
fi

echo "==> Radio stack installer"
echo "bundle_dir: ${BUNDLE_DIR:-not-detected}"
echo "radio_venv: ${RADIO_VENV}"
if [[ "${WITH_NEMO}" -eq 1 ]]; then
  echo "radio_asr_venv: ${RADIO_ASR_VENV}"
fi

if [[ "${NO_APT}" -eq 0 ]]; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "apt-get not found; re-run with --no-apt and install system packages manually." >&2
    exit 1
  fi
  echo "==> Installing system packages"
  sudo apt-get update
  sudo apt-get install -y "${APT_PACKAGES[@]}"
fi

mkdir -p "${HOME}/.venvs" "${LOCAL_BIN}"

echo "==> Installing Python packages into user environment"
python3 -m pip install --user --upgrade pip setuptools wheel
python3 -m pip install --user --upgrade "${USER_PIP_PACKAGES[@]}"

echo "==> Creating multichannel runtime venv"
python3 -m venv --system-site-packages "${RADIO_VENV}"
"${RADIO_VENV}/bin/python" -m pip install --upgrade pip setuptools wheel
"${RADIO_VENV}/bin/python" -m pip install --upgrade numpy openai PyYAML vosk

if [[ "${WITH_NEMO}" -eq 1 ]]; then
  echo "==> Creating NeMo ASR runtime"
  python3 -m venv "${RADIO_ASR_VENV}"
  "${RADIO_ASR_VENV}/bin/python" -m pip install --upgrade pip setuptools wheel
  "${RADIO_ASR_VENV}/bin/python" -m pip install --upgrade torch 'nemo_toolkit[asr]'
fi

if [[ -n "${BUNDLE_DIR}" && -x "${BUNDLE_DIR}/bin/radio" ]]; then
  echo "==> Linking radio wrapper into ~/.local/bin"
  ln -sf "${BUNDLE_DIR}/bin/radio" "${LOCAL_BIN}/radio"
fi

cat <<EOF

Install complete.

Next useful checks:
  ${LOCAL_BIN}/radio session
  ${LOCAL_BIN}/radio cortex status
  python3 -m py_compile ${BUNDLE_DIR:-\$HOME/random}/radio-cortex/llama_worker.py

Optional setup still on you:
  - export OPENAI_API_KEY=YOUR_API_KEY_HERE
  - if switching cortex back to a local backend, place a GGUF model at ~/.cache/models/Llama-3.2-1B-Instruct-Q4_K_M.gguf
  - if using NeMo, confirm ${RADIO_ASR_VENV}/bin/python can import torch and nemo.collections.asr
EOF
