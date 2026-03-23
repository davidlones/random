from __future__ import division
import math

from pyaudio import PyAudio # sudo apt-get install python{,3}-pyaudio

try:
    from itertools import izip
except ImportError: # Python 3
    izip = zip
    xrange = range

def sine_tone(frequency, duration, volume=1, sample_rate=22050):
    n_samples = int(sample_rate * duration)
    restframes = n_samples % sample_rate

    p = PyAudio()
    stream = p.open(format=p.get_format_from_width(1), # 8bit
                    channels=1, # mono
                    rate=sample_rate,
                    output=True)
    s = lambda t: volume * math.sin(2 * math.pi * frequency * t / sample_rate)
    samples = (int(s(t) * 0x7f + 0x80) for t in xrange(n_samples))
    for buf in izip(*[samples]*sample_rate): # write several samples at a time
        stream.write(bytes(bytearray(buf)))

    # fill remainder of frameset with silence
    stream.write(b'\x80' * restframes)

    stream.stop_stream()
    stream.close()
    p.terminate()


C0 = 16.351
C0 = 17.324
D0 = 18.354
Ds0 = 19.445
E0 = 20.601
F0 = 21.827
Fs0 = 23.124
G0 = 24.499
Gs0 = 25.956
A0 = 27.5
As0 = 29.135
B0 = 30.868

C1 = 32.703
Cs1 = 34.648
D1 = 36.708
Ds1 = 38.891
E1 = 41.203
F1 = 43.654
Fs1 = 46.249
G1 = 48.999
Gs1 = 51.913
A1 = 55
As1 = 58.27
B1 = 61.735

C2 = 65.406
Cs2 = 69.296
D2 = 73.416
Ds2 = 77.782
E2 = 82.407
F2 = 87.307
Fs2 = 92.499
G2 = 97.999
Gs2 = 103.826
A2 = 110
As2 = 116.541
B2 = 123.471

C3 = 130.813
Cs3 = 138.591
D3 = 146.832
Ds3 = 155.563
E3 = 164.814
F3 = 174.614
Fs3 = 184.997
G3 = 195.998
Gs3 = 207.652
A3 = 220 
As3 = 233.082
B3 = 246.942

C4 = 261.626
Cs4 = 277.183
D4 = 293.665
Ds4 = 311.127
E4 = 329.628
F4 = 349.228
Fs4 = 369.994
G4 = 391.995
Gs4 = 415.305
A4 = 440
As4 = 466.164
B4 = 493.883

C5 = 523.251
Cs5 = 554.365
D5 = 587.33
Ds5 = 622.254
E5 = 659.255
F5 = 698.456
Fs5 = 739.989
G5 = 783.991
Gs5 = 830.609
A5 = 880
As5 = 932.328
B5 = 987.767

C6 = 1046.502
Cs6 = 1108.731
D6 = 1174.659
Ds6 = 1244.508
E6 = 1318.51
F6 = 1396.913
Fs6 = 1479.978
G6 = 1567.982
Gs6 = 1661.219
A6 = 1760
As6 = 1864.655
B6 = 1975.533

C7 = 2093.005
Cs7 = 2217.461
D7 = 2349.318
Ds7 = 2489.016
E7 = 2637.021
F7 = 2793.826
Fs7 = 2959.955
G7 = 3135.964
Gs7 = 3322.438
A7 = 3520
As7 = 3729.31
B7 = 3951.066

C8 = 4186.009
Cs8 = 4434.922
D8 = 4698.636
Ds8 = 4978.032
E8 = 5274.042
F8 = 5587.652
Fs8 = 5919.91
G8 = 6271.928
Gs8 = 6644.876
A8 = 7040
As8 = 7458.62
B8 = 7902.132

C9 = 8372.018
Cs9 = 8869.844
D9 = 9397.272
Ds9 = 9956.064
E9 = 10548.084
F9 = 11175.304
Fs9 = 11839.82
G9 = 12543.856
Gs9 = 13289.752
A9 = 14080
As9 = 14917.24
B9 = 15804.264



sine_tone(frequency=8372, duration=1)





