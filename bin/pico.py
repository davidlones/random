from machine import Pin
from machine import PWM
import utime
import random

led_pin = Pin(25, Pin.OUT)
led_pin2 = PWM(Pin(6))
pez_pin = PWM(Pin(0))

stepper_pins = [
    Pin(14, Pin.OUT),
    Pin(15, Pin.OUT),
    Pin(16, Pin.OUT),
    Pin(17, Pin.OUT),
]

def playtone(freq,vol=1000,led=False,end=False):
    if led:
        led_pin.value(1)
    pez_pin.duty_u16(vol)
    pez_pin.freq(freq)
    if end:
        utime.sleep(end)
        if led:
            led_pin.value(0)
        pez_pin.duty_u16(0)
        

def short():
    led_pin.value(1)
    playtone(3000)
    utime.sleep(0.1)
    led_pin.value(0)
    pez_pin.duty_u16(0)
    utime.sleep(0.1)
    
def long():
    led_pin.value(1)
    playtone(3000)
    utime.sleep(0.2)
    led_pin.value(0)
    pez_pin.duty_u16(0)
    utime.sleep(0.1)

def morse(code,input_type=False):
    print(code)
    if input_type == 'bin':
        code = bin(code)
        if code[:1] == '-':
            code = code[1:]
        code = code[2:]
        print(code)
    for char in code:
        if char == '.':
            playtone(3000,led=True,end=0.05)
        elif char == '-':
            playtone(3000,led=True,end=0.1)
        elif char == '0':
            playtone(3000,led=True,end=0.05)
        elif char == '1':
            playtone(3000,led=True,end=0.1)
        else:
            print("Invalid character in code:")
            print(char)
        utime.sleep(0.05)
    utime.sleep_ms(500)


def step(steps,delay=False,audio='on'):
    num_stepper_pins = len(stepper_pins)
    full_step_sequence = [[int(i==j) for j in range(num_stepper_pins)] for i in range(num_stepper_pins)]

    if steps < 0:
        full_step_sequence = full_step_sequence[::-1]
    steps = abs(round(steps))

    for count in range(steps):
        led_pin.value(1)
        if audio == 'on':
            playtone(50,50)
        for bit in full_step_sequence:
            for i in range(len(stepper_pins)):
                stepper_pins[i].value(bit[i])
                utime.sleep(0.001)
        led_pin.value(0)
        if audio == 'on':
            pez_pin.duty_u16(0)
        if delay:
            utime.sleep(delay)

def random_steps(rnd_steps):
    rand_ints = []
    morse("-.")
    for t in range(rnd_steps):
        rand_int = random.randint(-512, 512)
        rand_ints.append(rand_int)
        step(rand_int)
        # print(rand_int)
        morse(rand_int,'bin')

    final_step = sum(rand_ints) % 512
    if final_step > 256:
        final_step = final_step - 512
    # print(final_step)

    step(-1*final_step)
    morse(".-")

led_pin.value(1)
led_pin2.duty_u16(65535)
step(100)
step(-200)
step(100)
led_pin2.duty_u16(0)
pez_pin.duty_u16(0)
morse('...---...')
print('done')
