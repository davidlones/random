from machine import Pin, PWM
import utime, random

# Try to enable dual-core background execution (RP2040 supports this in MicroPython)
try:
    import _thread
    HAS_THREAD = True
except ImportError:
    HAS_THREAD = False

# =================================================
# GLOBAL STATE
# =================================================
running = False           # preserve original auto-boot behavior
stop_requested = False

# IRQ debounce
_last_press_ms = 0
_DEBOUNCE_MS = 200

# Optional lock for shared state (thread-safe-ish)
_lock = _thread.allocate_lock() if HAS_THREAD else None

# =================================================
# HARDWARE SETUP
# =================================================
led_main = Pin(25, Pin.OUT)
led_amb  = PWM(Pin(4))
piezo    = PWM(Pin(0))

piezo.duty_u16(0)
led_amb.duty_u16(0)

stepper_pins = [Pin(p, Pin.OUT) for p in (14, 15, 16, 17)]

# Button (GPIO 18 → GND, pull-up)
button = Pin(18, Pin.IN, Pin.PULL_UP)

# =================================================
# STEPPER CONFIGURATION
# =================================================
HALF_STEP = (
    (1,0,0,0),(1,1,0,0),(0,1,0,0),(0,1,1,0),
    (0,0,1,0),(0,0,1,1),(0,0,0,1),(1,0,0,1)
)
HALF_STEP_REV = tuple(reversed(HALF_STEP))  # reusable, not an iterator

phase = 0
direction = 1

def coils(bits):
    for i, v in enumerate(bits):
        stepper_pins[i].value(v)

def stepper_off():
    coils((0,0,0,0))

def all_off():
    led_main.value(0)
    piezo.duty_u16(0)
    led_amb.duty_u16(0)
    stepper_off()

def panic_stop():
    piezo.duty_u16(0)
    stepper_off()
    led_main.value(0)

# =================================================
# INTERRUPT CONTROL
# =================================================
def _set_flags(run=None, stop=None):
    """Small helper so thread/no-thread paths stay consistent."""
    global running, stop_requested
    if _lock:
        with _lock:
            if run is not None:
                running = run
            if stop is not None:
                stop_requested = stop
    else:
        if run is not None:
            running = run
        if stop is not None:
            stop_requested = stop

def _get_flags():
    global running, stop_requested
    if _lock:
        with _lock:
            return running, stop_requested
    return running, stop_requested

def check_stop():
    global running, stop_requested
    r, s = _get_flags()
    if s:
        _set_flags(run=False, stop=False)
        panic_stop()
        raise KeyboardInterrupt

def button_irq(pin):
    global _last_press_ms

    now = utime.ticks_ms()
    if utime.ticks_diff(now, _last_press_ms) < _DEBOUNCE_MS:
        return
    _last_press_ms = now

    # Active-low press
    if pin.value() == 0:
        r, _ = _get_flags()
        if r:
            _set_flags(stop=True)
        else:
            _set_flags(run=True)

button.irq(trigger=Pin.IRQ_FALLING, handler=button_irq)

# =================================================
# NOTES
# =================================================
NOTES = {
    "C4":262,"D4":294,"E4":330,"F4":349,"G4":392,
    "A4":440,"B4":494,
    "C5":523,"D5":587,"E5":659,"G5":784,"A5":880,
    "REST":0
}

# =================================================
# AUDIO
# =================================================
def playtone(freq, vol=6000, led=False, end=False):
    if led:
        led_main.value(1)
    if freq and freq > 0:
        piezo.freq(int(freq))
        piezo.duty_u16(int(vol))
    if end:
        utime.sleep(end)
        piezo.duty_u16(0)
        led_main.value(0)

def piezo_accent(freq, dur_ms, vol=1600):
    if not freq or freq <= 0:
        piezo.duty_u16(0)
        utime.sleep_ms(int(dur_ms))
        return
    piezo.freq(int(freq))
    led_amb.freq(int(freq))
    piezo.duty_u16(int(vol))
    led_amb.duty_u16(int(vol))
    utime.sleep_ms(int(dur_ms))
    piezo.duty_u16(0)
    led_amb.duty_u16(0)

# =================================================
# LED
# =================================================
def breathe_led(pwm_freq=1000, breath_ms=1200, max_duty=30000, steps=64):
    led_amb.freq(int(pwm_freq))

    half = breath_ms // 2
    step_delay = max(1, half // steps)

    # Inhale
    for i in range(steps):
        check_stop()
        duty = (max_duty * i) // steps
        led_amb.duty_u16(duty)
        utime.sleep_ms(step_delay)

    # Exhale
    for i in range(steps, -1, -1):
        check_stop()
        duty = (max_duty * i) // steps
        led_amb.duty_u16(duty)
        utime.sleep_ms(step_delay)

    led_amb.duty_u16(0)

# =================================================
# MUSICAL STEPPER (more stable scheduling)
# =================================================
def play_stepper_note(freq, duration_ms, led=True):
    global phase, direction

    if not freq or freq <= 0:
        utime.sleep_ms(int(duration_ms))
        return

    interval_us = int(1_000_000 / int(freq))
    interval_us = max(200, interval_us)

    duration_ms = int(duration_ms)
    end_us = utime.ticks_add(utime.ticks_us(), duration_ms * 1000)
    next_us = utime.ticks_us()

    if led:
        led_main.value(1)

    while utime.ticks_diff(end_us, utime.ticks_us()) > 0:
        check_stop()
        now = utime.ticks_us()
        if utime.ticks_diff(now, next_us) >= 0:
            phase = (phase + direction) % len(HALF_STEP)
            coils(HALF_STEP[phase])
            direction = -direction
            next_us = utime.ticks_add(next_us, interval_us // 2)
        else:
            utime.sleep_us(50)

    if led:
        led_main.value(0)
    stepper_off()

# =================================================
# POSITIONAL STEPPER (bug-fixed)
# =================================================
def step(steps, delay=False, audio='on'):
    seq = HALF_STEP if steps >= 0 else HALF_STEP_REV
    steps = abs(int(steps))
    if steps == 0:
        return

    for _ in range(steps):
        check_stop()
        for bits in seq:
            check_stop()
            coils(bits)
            if audio == 'on':
                piezo_accent(50, 3, 200)
            utime.sleep_ms(1)
        if delay:
            utime.sleep(delay)

    stepper_off()

# =================================================
# MORSE (binary capable, text-agnostic)
# =================================================
def morse(code, input_type=False):
    if input_type == 'bin':
        code = bin(code).replace('-', '')[2:]

    for c in code:
        check_stop()
        if c == '.':
            piezo_accent(3000, 100)
        elif c == '-':
            piezo_accent(3000, 300)
        elif c == '0':
            piezo_accent(2000, 60)
        elif c == '1':
            piezo_accent(2000, 120)
        elif c == ' ':
            utime.sleep_ms(80)
        utime.sleep_ms(60)

    utime.sleep_ms(400)

# =================================================
# RANDOM WALK + RECONCILIATION
# =================================================
def random_steps(n=1):
    log = []
    morse("-.")
    for _ in range(int(n)):
        check_stop()
        r = random.randint(-512, 512)
        log.append(r)
        step(r)
        morse(r, 'bin')

    correction = sum(log) % 512
    if correction > 256:
        correction -= 512

    step(-correction)
    morse(".-")

# =================================================
# MUSIC HELPERS (more flexible)
# =================================================
def _note_to_hz(note):
    if isinstance(note, (int, float)):
        return int(note)
    return int(NOTES.get(note, 0))

def play_phrase(phrase, tempo=120, led=True):
    beat_ms = int(60000 / int(tempo))
    for note, beats in phrase:
        check_stop()
        hz = _note_to_hz(note)
        play_stepper_note(hz, int(beats) * beat_ms, led=led)
        utime.sleep_ms(20)

def arpeggio(root):
    return [(root, 1), ("E5", 1), ("G5", 1), ("A5", 1)]

# =================================================
# SOL FINALE
# =================================================
def sol():
    unit = 120
    tone = 880

    def dit():
        check_stop()
        led_main.value(1)
        piezo_accent(tone * 2, unit, 2000)
        play_stepper_note(tone, unit, led=False)
        led_main.value(0)
        utime.sleep_ms(unit)

    def dah():
        check_stop()
        led_main.value(1)
        piezo_accent(tone * 2, unit * 3, 2000)
        play_stepper_note(tone, unit * 3, led=False)
        led_main.value(0)
        utime.sleep_ms(unit)

    dit(); dit(); dit()
    utime.sleep_ms(unit * 2)
    dah(); dah(); dah()
    utime.sleep_ms(unit * 2)
    dit(); dah(); dit(); dit()

def mario_phrase():
    notes = (
        660,660,0,660,0,523,660,0,784,0,392,
        523,0,392,0,330,0,440,0,494,0,466,440,
        0,
        784,659,784,880,698,784,659,523,587,494
    )

    for n in notes:
        check_stop()
        play_stepper_note(n, 120)
        if n > 0:
            piezo_accent(n * 2, 30, 1200)

def boot_phrase():
    play_phrase(arpeggio("A4"), tempo=140)
    mario_phrase()

    for _ in range(4):
        check_stop()
        note = random.choice(("C4","D4","E4","G4","A4","C5"))
        play_stepper_note(NOTES[note], 200)
        if random.random() < 0.3:
            piezo_accent(NOTES[note] * 2, 40)

# =================================================
# BOOT PERFORMANCE
# =================================================
def boot_sequence():
    global running
    utime.sleep_ms(200)
    breathe_led()

    try:
        boot_phrase()
        random_steps(1)
        sol()

    except KeyboardInterrupt:
        pass

    finally:
        all_off()
        breathe_led(10, 600)
        _set_flags(run=False, stop=False)

# =================================================
# BACKGROUND RUNNER (threaded)
# =================================================
def _runner():
    while True:
        try:
            r, _ = _get_flags()
            if r:
                boot_sequence()
        except KeyboardInterrupt:
            # expected: stop requested mid-performance
            pass
        except Exception as e:
            # unexpected: log once, then recover
            all_off()
        utime.sleep_ms(20)

# =================================================
# MAIN
# =================================================
if __name__ == "__main__":
    if HAS_THREAD:
        # Run performances in the background so REPL stays available.
        _thread.start_new_thread(_runner, ())
        # main.py exits here -> REPL returns while background keeps running
    else:
        # Fallback: single-core behavior (REPL will be blocked while running)
        while True:
            r, _ = _get_flags()
            if r:
                boot_sequence()
            utime.sleep_ms(50)