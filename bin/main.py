from machine import Pin, PWM
import utime, random

# =================================================
# HARDWARE SETUP
# =================================================
led_main = Pin(25, Pin.OUT)
led_amb  = PWM(Pin(4))
piezo    = PWM(Pin(0))
piezo.duty_u16(0)

stepper_pins = [Pin(p, Pin.OUT) for p in (14, 15, 16, 17)]

# =================================================
# STEPPER CONFIGURATION
# =================================================
HALF_STEP = (
    (1,0,0,0),(1,1,0,0),(0,1,0,0),(0,1,1,0),
    (0,0,1,0),(0,0,1,1),(0,0,0,1),(1,0,0,1)
)
HALF_STEP_REV = tuple(reversed(HALF_STEP))  # IMPORTANT: reusable, not an iterator

phase = 0
direction = 1

def coils(bits):
    # bits is a 4-tuple of 0/1 values
    for i, v in enumerate(bits):
        stepper_pins[i].value(v)

def stepper_off():
    coils((0,0,0,0))

def all_off():
    # a single “lights out” helper for reliable cleanup
    led_main.value(0)
    piezo.duty_u16(0)
    led_amb.duty_u16(0)
    stepper_off()

def panic_stop():
    # use when things get weird mid-performance
    piezo.duty_u16(0)
    stepper_off()
    led_main.value(0)

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
    piezo.duty_u16(int(vol))
    utime.sleep_ms(int(dur_ms))
    piezo.duty_u16(0)

# =================================================
# LED
# =================================================
def breathe_led(step=1024, delay_ms=1):
    step = max(1, int(step))
    delay_ms = max(0, int(delay_ms))
    for i in range(0, 65535, step):
        led_amb.duty_u16(i)
        utime.sleep_ms(delay_ms)
    for i in range(65535, -1, -step):
        led_amb.duty_u16(i)
        utime.sleep_ms(delay_ms)
    led_amb.duty_u16(0)

# =================================================
# MUSICAL STEPPER (more stable scheduling)
# =================================================
def play_stepper_note(freq, duration_ms, led=True):
    """
    Stepper-as-oscillator. We advance coil phase at a scheduled cadence.
    This reduces pitch jitter vs naive sleep loops.
    """
    global phase, direction

    if not freq or freq <= 0:
        utime.sleep_ms(int(duration_ms))
        return

    # interval between coil events (half-cycle-ish for your back-and-forth feel)
    interval_us = int(1_000_000 / int(freq))
    interval_us = max(200, interval_us)  # guard: too-small intervals will thrash CPU/driver

    duration_ms = int(duration_ms)
    end_us = utime.ticks_add(utime.ticks_us(), duration_ms * 1000)
    next_us = utime.ticks_us()

    if led:
        led_main.value(1)

    while utime.ticks_diff(end_us, utime.ticks_us()) > 0:
        now = utime.ticks_us()
        if utime.ticks_diff(now, next_us) >= 0:
            # do one step “tick”
            phase = (phase + direction) % len(HALF_STEP)
            coils(HALF_STEP[phase])

            # your floppy-style bounce
            direction = -direction

            # schedule next tick
            next_us = utime.ticks_add(next_us, interval_us // 2)

        else:
            # tiny yield to avoid pegging CPU
            utime.sleep_us(50)

    if led:
        led_main.value(0)
    stepper_off()

# =================================================
# POSITIONAL STEPPER (bug-fixed)
# =================================================
def step(steps, delay=False, audio='on'):
    """
    Original “positional” stepping.
    Fix: use a reusable reversed sequence, not a consumed iterator.
    """
    seq = HALF_STEP if steps >= 0 else HALF_STEP_REV
    steps = abs(int(steps))
    if steps == 0:
        return

    for _ in range(steps):
        for bits in seq:
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
    # allow either "A4" or raw numeric frequencies
    if isinstance(note, (int, float)):
        return int(note)
    return int(NOTES.get(note, 0))

def play_phrase(phrase, tempo=120, led=True):
    beat_ms = int(60000 / int(tempo))
    for note, beats in phrase:
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
        led_main.value(1)
        piezo_accent(tone * 2, unit, 2000)
        play_stepper_note(tone, unit, led=False)
        led_main.value(0)
        utime.sleep_ms(unit)

    def dah():
        led_main.value(1)
        piezo_accent(tone * 2, unit * 3, 2000)
        play_stepper_note(tone, unit * 3, led=False)
        led_main.value(0)
        utime.sleep_ms(unit)

    # “SOL” rhythm
    dit(); dit(); dit()
    utime.sleep_ms(unit * 2)
    dah(); dah(); dah()
    utime.sleep_ms(unit * 2)
    dit(); dah(); dit(); dit()

# =================================================
# BOOT PERFORMANCE
# =================================================
def boot_sequence():
    utime.sleep_ms(200)
    breathe_led()

    try:
        for _ in range(2):
            play_phrase(arpeggio("A4"), tempo=140)
            breathe_led(step=2048)

        for _ in range(4):
            note=random.choice(("C4","D4","E4","G4","A4","C5"))
            play_stepper_note(NOTES[note],200)
            if random.random()<0.3:
                piezo_accent(NOTES[note]*2,40)

        random_steps(1)
        sol()

    finally:
        stepper_off()
        piezo.duty_u16(0)
        led_amb.duty_u16(1000)


if __name__ == "__main__":
    boot_sequence()
