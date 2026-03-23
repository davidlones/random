#!/bin/bash

target_path="$1"
port="${2:-8000}"

if [ -z "$target_path" ]; then
    echo "Usage: $0 <file-or-directory> [port]"
    exit 1
fi

if [ -f "$target_path" ] || [ -d "$target_path" ]; then
    exec python3 /home/david/random/bin/share.py "$target_path" -p "$port"
else
    echo "Invalid file or directory path."
    exit 1
fi
