#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from radio_config import DEFAULT_CONFIG_PATH
ROOT = Path.home() / "random" / "radio-cortex"
WORKER = ROOT / "llama_worker.py"
DEFAULT_CONFIG = DEFAULT_CONFIG_PATH


def main() -> int:
    args = sys.argv[1:] or ["status"]
    all_local = False
    if "--all-local" in args:
        args = [arg for arg in args if arg != "--all-local"]
        all_local = True
    if "--config" not in args:
        args.extend(["--config", str(DEFAULT_CONFIG)])
    env = os.environ.copy()
    if all_local:
        env["RADIO_CORTEX_ALL_LOCAL"] = "1"
    proc = subprocess.run([sys.executable, str(WORKER), *args], env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
