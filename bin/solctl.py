#!/usr/bin/env python3
"""
solctl.py — System-to-Signal Bridge (pressure-aware, musical)

Host-side conductor that drives a MicroPython Pico “instrument” (main.py)
via mpremote exec calls.

Features:
- CPU + memory pressure sampling (Linux-native)
- Pressure mapped to timing, brightness, motion, and sound
- Deterministic identity signals (binary morse, motion, tone)
- Musical identity phrase (Mario theme) using stepper + piezo
- Safe execution: reset, link check, graceful skipping, cleanup
"""

import argparse
import datetime
import os
import platform
import shutil
import subprocess
import sys
import time
import textwrap

# -------------------------------------------------
# configuration
# -------------------------------------------------

DEFAULT_TONE_MIN = 300
DEFAULT_TONE_SPAN = 700

CPU_TIME_WEIGHT    = 0.6
CPU_ENTROPY_WEIGHT = 0.4
MEM_RANGE_WEIGHT   = 1.0
MEM_DIM_WEIGHT     = 0.6

# Musical identity phrase (Mario theme)
MARIO_PHRASE = (
    660,660,0,660,0,523,660,0,784,0,392,
    523,0,392,0,330,0,440,0,494,0,466,440,
    0,
    784,659,784,880,698,784,659,523,587,494
)

# -------------------------------------------------
# cli
# -------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        prog="solctl",
        description="Host-to-Pico signal bridge using mpremote + main.py"
    )
    p.add_argument("--device", help="Optional mpremote device selector")
    p.add_argument("--no-reset", action="store_true", help="Skip Pico reset")
    p.add_argument("--dry-run", action="store_true", help="Print commands only")
    p.add_argument("--quiet", action="store_true", help="Reduce explanatory output")
    return p.parse_args()

# -------------------------------------------------
# mpremote helpers
# -------------------------------------------------

def mpremote_path():
    mp = shutil.which("mpremote")
    if not mp:
        raise RuntimeError("mpremote not found in PATH")
    return mp

def mp_args(device=None):
    return f"-p {device}" if device else ""

def _run_cmd(cmd, dry_run=False):
    if dry_run:
        return 0
    return subprocess.run(cmd, shell=True).returncode

def run_mp(code, device=None, dry_run=False, explain=None, quiet=False):
    mp = mpremote_path()
    cmd = f'{mp} {mp_args(device)} exec "{code}"'.strip()
    if explain and not quiet:
        print(f"\n→ {explain}")
    print(f"  {cmd}")
    return _run_cmd(cmd, dry_run) == 0

def run_mp_reset(device=None, dry_run=False, explain=None, quiet=False):
    mp = mpremote_path()
    cmd = f"{mp} {mp_args(device)} reset".strip()
    if explain and not quiet:
        print(f"\n→ {explain}")
    print(f"  {cmd}")
    return _run_cmd(cmd, dry_run) == 0

# -------------------------------------------------
# helpers
# -------------------------------------------------

def banner(title, width=60):
    print("\n" + "=" * width)
    print(title)
    print("=" * width)

def soft_fail(msg):
    print(f"  [!] {msg}")

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def stable_hash_int(s, modulus, center=0):
    val = sum((i + 1) * ord(c) for i, c in enumerate(s))
    return (val % modulus) - center

def to_bin_string(s):
    return "".join(f"{ord(c):08b}" for c in s)

def bin_string_to_int(bits):
    return int(bits, 2) if bits else 0

# -------------------------------------------------
# system pressure (Linux)
# -------------------------------------------------

def read_cpu_load_norm():
    try:
        load1, _, _ = os.getloadavg()
        return load1 / (os.cpu_count() or 1)
    except Exception:
        return 0.0

def read_mem_pressure():
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                mem[k.strip()] = int(v.strip().split()[0])
        total = mem["MemTotal"]
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        return max(0, total - avail) / total
    except Exception:
        return 0.0

def pressure_beep_params(cpu_norm, mem_p):
    freq   = int(400 + cpu_norm * 1200)
    vol    = int(600 + mem_p * (8000 - 600))
    dur    = int(60 + cpu_norm * 120)
    pulses = int(2 + cpu_norm * 3 + mem_p * 3)
    return freq, dur, vol, pulses

def cpu_scaled_duration(ms, cpu_norm):
    factor = 1.0 / (1.0 + cpu_norm * CPU_TIME_WEIGHT)
    return max(60, int(ms * factor))

# -------------------------------------------------
# phase runner
# -------------------------------------------------

def phase(label, fn):
    banner(label)
    try:
        fn()
    except Exception as e:
        soft_fail(f"Phase failed: {e!r}")

# -------------------------------------------------
# main
# -------------------------------------------------

def main():
    args = parse_args()

    host = platform.node() or "unknown"
    os_str = f"{platform.system()} {platform.release()}"
    arch = platform.machine() or "unknown"
    now = datetime.datetime.now().isoformat(timespec="seconds")

    cpu_norm = clamp(read_cpu_load_norm(), 0.0, 2.0)
    mem_p    = clamp(read_mem_pressure(), 0.0, 1.0)

    banner("SOLCTL — System-to-Signal Bridge")
    print(textwrap.dedent(f"""\
    Host Name : {host}
    OS        : {os_str}
    Arch      : {arch}
    Time      : {now}
    Device    : {args.device or "(auto)"}
    """).strip())

    if not args.quiet:
        print(f"\nCPU load norm   : {cpu_norm:.2f}")
        print(f"Memory pressure : {mem_p:.2f}")

    # -----------------------------------------
    # Preflight reset
    # -----------------------------------------
    if not args.no_reset:
        banner("Preflight — Reset Pico")
        run_mp_reset(args.device, args.dry_run, "Reset Pico", args.quiet)
        time.sleep(1.0)

    # -----------------------------------------
    # Link check
    # -----------------------------------------
    phase("Link Check", lambda: run_mp(
        "import main as m; 1",
        args.device, args.dry_run,
        "Confirm Pico can import main",
        args.quiet
    ))

    # -----------------------------------------
    # Phase 0 — system pressure pulse
    # -----------------------------------------
    def p0():
        freq, dur, vol, pulses = pressure_beep_params(cpu_norm, mem_p)
        if not args.quiet:
            print(f"\nPressure beeps: freq={freq}Hz vol={vol} dur={dur}ms pulses={pulses}")
        for i in range(pulses):
            run_mp(
                f"import main as m; m.piezo_accent({freq},{dur},{vol})",
                args.device, args.dry_run,
                f"Pressure pulse {i+1}/{pulses}",
                args.quiet
            )
            time.sleep(max(0.05, 0.25 - cpu_norm * 0.15))

    phase("Phase 0 — System Pressure Pulse", p0)

    # -----------------------------------------
    # Phase 1 — presence & wake
    # -----------------------------------------
    def p1():
        step  = max(128, int(2048 / (1.0 + cpu_norm)))
        delay = max(0, int(2 / (1.0 + cpu_norm)))
        run_mp(
            f"import main as m; m.breathe_led(step={step},delay_ms={delay})",
            args.device, args.dry_run,
            "Ambient LED breathing",
            args.quiet
        )
        amb = int(65535 * (1.0 - mem_p * MEM_DIM_WEIGHT))
        run_mp(
            f"import main as m; m.led_amb.duty_u16({amb})",
            args.device, args.dry_run,
            "Memory-shaped ambient level",
            args.quiet
        )

    phase("Phase 1 — Presence & Wake", p1)

    # -----------------------------------------
    # Phase 2 — host identity (binary morse)
    # -----------------------------------------
    def p2():
        bits = to_bin_string(host)
        host_int = bin_string_to_int(bits)
        run_mp(
            f"import main as m; m.morse({host_int},'bin')",
            args.device, args.dry_run,
            "Host identity as binary morse",
            args.quiet
        )

    phase("Phase 2 — Host Identity", p2)

    # -----------------------------------------
    # Phase 2.5 — musical identity (Mario)
    # -----------------------------------------
    def p2_5():
        if cpu_norm > 1.2:
            if not args.quiet:
                print("\nSkipping musical phrase — CPU pressure high.")
            return

        note_dur  = cpu_scaled_duration(120, cpu_norm)
        piezo_dur = max(20, int(note_dur * 0.25))
        piezo_vol = int(800 + mem_p * 1200)

        phrase = ",".join(str(n) for n in MARIO_PHRASE)

        run_mp(
            f"import main as m; "
            f"[(m.play_stepper_note(n,{note_dur}),"
            f"  m.piezo_accent(n*2,{piezo_dur},{piezo_vol})) "
            f" for n in ({phrase})]",
            args.device, args.dry_run,
            "Musical identity — Mario theme",
            args.quiet
        )

    phase("Phase 2.5 — Musical Signature", p2_5)

    # -----------------------------------------
    # Phase 3 — architecture as motion
    # -----------------------------------------
    def p3():
        raw = stable_hash_int(arch, 1024, 512)
        scaled = int(raw * max(0.2, 1.0 - mem_p))
        run_mp(
            f"import main as m; m.step({scaled})",
            args.device, args.dry_run,
            "Architecture as constrained motion",
            args.quiet
        )

    phase("Phase 3 — Architecture as Motion", p3)

    # -----------------------------------------
    # Phase 4 — OS signature tone
    # -----------------------------------------
    def p4():
        base = stable_hash_int(os_str, DEFAULT_TONE_SPAN, 0) % DEFAULT_TONE_SPAN
        tone = DEFAULT_TONE_MIN + base
        dur  = cpu_scaled_duration(1200, cpu_norm)
        run_mp(
            f"import main as m; m.play_stepper_note({tone},{dur})",
            args.device, args.dry_run,
            "OS signature tone",
            args.quiet
        )

    phase("Phase 4 — OS Signature", p4)

    # -----------------------------------------
    # Phase 5 — temporal entropy
    # -----------------------------------------
    def p5():
        sec = datetime.datetime.now().second
        entropy = ((sec * 13) % 512) - 256
        entropy = int(entropy * (1.0 + cpu_norm * CPU_ENTROPY_WEIGHT))
        entropy = int(entropy * max(0.3, 1.0 - mem_p))
        entropy = clamp(entropy, -512, 512)
        run_mp(
            f"import main as m; m.step({entropy})",
            args.device, args.dry_run,
            "Temporal entropy motion",
            args.quiet
        )

    phase("Phase 5 — Temporal Entropy", p5)

    # -----------------------------------------
    # Phase 6 — finale
    # -----------------------------------------
    phase("Phase 6 — SOL Finale", lambda: run_mp(
        "import main as m; m.sol()",
        args.device, args.dry_run,
        "SOL closing cadence",
        args.quiet
    ))

    # -----------------------------------------
    # Cleanup
    # -----------------------------------------
    phase("Shutdown & Cleanup", lambda: run_mp(
        "import main as m; m.all_off()",
        args.device, args.dry_run,
        "Return Pico to quiescent state",
        args.quiet
    ))

    print("\nSOLCTL complete.")

# -------------------------------------------------

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted — emergency stop.")
        try:
            run_mp("import main as m; m.panic_stop()", quiet=True)
            run_mp("import main as m; m.all_off()", quiet=True)
        except Exception:
            pass
        sys.exit(130)
