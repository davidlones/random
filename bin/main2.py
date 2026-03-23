from machine import Pin, PWM
import utime, random

# =================================================
# OPTIONAL: RP2040 PIO SUPPORT
# =================================================
PIO_AVAILABLE = False
try:
    import rp2
    PIO_AVAILABLE = True
except Exception:
    rp2 = None
    PIO_AVAILABLE = False

# =================================================
# HARDWARE SETUP
# =================================================
led_main = Pin(25, Pin.OUT)
led_amb  = PWM(Pin(4))
piezo    = PWM(Pin(0))
piezo.duty_u16(0)

STEPPER_GPIO = (14, 15, 16, 17)
stepper_pins = [Pin(p, Pin.OUT) for p in STEPPER_GPIO]

# =================================================
# STEPPER CONFIGURATION
# =================================================
HALF_STEP = (
    (1,0,0,0),(1,1,0,0),(0,1,0,0),(0,1,1,0),
    (0,0,1,0),(0,0,1,1),(0,0,0,1),(1,0,0,1)
)
HALF_STEP_REV = tuple(reversed(HALF_STEP))

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
    _pio_stepper_stop()

def panic_stop():
    piezo.duty_u16(0)
    stepper_off()
    led_main.value(0)
    _pio_stepper_stop()

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
# PIO STEPPER BACKEND
# =================================================
_PIO_SM_ID = 0
_pio_sm = None

def _bits_to_nibble(bits):
    return (bits[0] & 1) | ((bits[1] & 1) << 1) | ((bits[2] & 1) << 2) | ((bits[3] & 1) << 3)

def _pack_8_nibbles(nibs):
    w = 0
    for i, n in enumerate(nibs):
        w |= (n & 0xF) << (i * 4)
    return w

if PIO_AVAILABLE:
    @rp2.asm_pio(
        out_init=(rp2.PIO.OUT_LOW,)*4,
        out_shiftdir=rp2.PIO.SHIFT_RIGHT,
        autopull=True,
        pull_thresh=4,
    )
    def _pio_stepper_out():
        pull(block)
        out(pins, 4)

def _pio_stepper_start(freq):
    global _pio_sm
    if not PIO_AVAILABLE:
        return False
    if _pio_sm is None:
        _pio_sm = rp2.StateMachine(
            _PIO_SM_ID,
            _pio_stepper_out,
            freq=freq,
            out_base=Pin(STEPPER_GPIO[0])
        )
    else:
        _pio_sm.init(_pio_stepper_out, freq=freq, out_base=Pin(STEPPER_GPIO[0]))
    _pio_sm.active(1)
    return True

def _pio_stepper_stop():
    global _pio_sm
    if _pio_sm:
        try:
            _pio_sm.active(0)
        except Exception:
            pass
    stepper_off()

def _pio_stream(nibbles):
    if not _pio_sm:
        return False
    buf = []
    for n in nibbles:
        buf.append(n & 0xF)
        if len(buf) == 8:
            _pio_sm.put(_pack_8_nibbles(buf))
            buf.clear()
    if buf:
        _pio_sm.put(_pack_8_nibbles(buf))
    return True

# =================================================
# MUSICAL STEPPER
# =================================================
def play_stepper_note(freq, duration_ms, led=True):
    global phase, direction

    duration_ms = int(duration_ms)
    if not freq or freq <= 0:
        utime.sleep_ms(duration_ms)
        return

    freq = int(freq)
    event_hz = max(1, freq * 2)
    sm_freq = min(200_000, max(2_000, event_hz * 2))

    if PIO_AVAILABLE and _pio_stepper_start(sm_freq):
        if led:
            led_main.value(1)

        events = max(1, int(event_hz * duration_ms / 1000))

        def gen():
            global phase, direction
            for _ in range(events):
                phase = (phase + direction) % len(HALF_STEP)
                yield _bits_to_nibble(HALF_STEP[phase])
                direction = -direction

        _pio_stream(gen())
        utime.sleep_ms(duration_ms)
        if led:
            led_main.value(0)
        _pio_stepper_stop()
        return

    # Fallback GPIO
    interval_us = max(200, int(1_000_000 / freq))
    end_us = utime.ticks_add(utime.ticks_us(), duration_ms * 1000)
    next_us = utime.ticks_us()

    if led:
        led_main.value(1)

    while utime.ticks_diff(end_us, utime.ticks_us()) > 0:
        if utime.ticks_diff(utime.ticks_us(), next_us) >= 0:
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
# MUSIC HELPERS
# =================================================
def _note_to_hz(note):
    if isinstance(note, (int, float)):
        return int(note)
    return NOTES.get(note, 0)

def play_phrase(phrase, tempo=120, led=True):
    beat_ms = int(60000 / tempo)
    for note, beats in phrase:
        play_stepper_note(_note_to_hz(note), beat_ms * beats, led)
        utime.sleep_ms(20)

def arpeggio(root):
    return [(root,1),("E5",1),("G5",1),("A5",1)]

# =================================================
# SOL FINALE
# =================================================
def sol():
    unit = 120
    tone = 880

    def dit():
        led_main.value(1)
        piezo_accent(tone*2, unit, 2000)
        play_stepper_note(tone, unit, False)
        led_main.value(0)
        utime.sleep_ms(unit)

    def dah():
        led_main.value(1)
        piezo_accent(tone*2, unit*3, 2000)
        play_stepper_note(tone, unit*3, False)
        led_main.value(0)
        utime.sleep_ms(unit)

    dit(); dit(); dit()
    utime.sleep_ms(unit*2)
    dah(); dah(); dah()
    utime.sleep_ms(unit*2)
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
        sol()
    finally:
        all_off()

if __name__ == "__main__":
    boot_sequence()
