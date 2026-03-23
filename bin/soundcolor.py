import sys
import numpy as np
from PyQt5 import QtGui, QtWidgets
from PyQt5.QtCore import Qt
import wave
import alsaaudio
from scipy.fftpack import fft, fftfreq

class SoundVisualizer(QtWidgets.QWidget):
    def __init__(self, sound_file):
        super().__init__()

        # Set up the audio output device
        self.out = alsaaudio.PCM(alsaaudio.PCM_PLAYBACK)
        self.out.setchannels(2)
        self.out.setrate(44100)
        self.out.setformat(alsaaudio.PCM_FORMAT_S16_LE)

        # Store the sound file to visualize
        self.sound_file = sound_file

        # Set up the user interface
        self.initUI()

    def initUI(self):
        self.setGeometry(300, 300, 500, 500)
        self.setWindowTitle('Sound Visualizer')
        self.show()

    def paintEvent(self, event):
        qp = QtGui.QPainter()
        qp.begin(self)
        self.drawSound(qp)
        qp.end()

    def drawSound(self, qp):
        # Read in the sound data
        with wave.open(self.sound_file, 'r') as wav_file:
            num_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            num_frames = wav_file.getnframes()

            data = wav_file.readframes(num_frames)
            data = np.frombuffer(data, dtype=np.int16)

        # Play the sound
        self.out.write(data)

        # Convert the sound data to frequency domain using FFT
        yf = fft(data)
        freqs = fftfreq(len(data), 1/sample_rate)

        # Map the sound frequencies to light frequencies
        freqs_light = 400 + (800-400)*(freqs-20)/(20000-20)

        # Convert the light frequencies to RGB colors
        colors = self.wavelength_to_rgb(freqs_light)

        # Map the sound amplitude to the image brightness
        brightness = np.abs(data)
        brightness = brightness / np.max(brightness) # Normalize the brightness values
        brightness = (255*brightness).astype(int) # Scale the brightness values to the range [0, 255]

        # Draw the sound data as a series of colored pixels
        for i, color in enumerate(colors):
            qp.setPen(QtGui.QColor(*color, brightness[i]))
            qp.drawPoint(i, i)

    def wavelength_to_rgb(self, wavelength):
        """
        Convert a wavelength in the range [400, 800] to an RGB color.
        """
        w = int(wavelength)

        # Color spectrum is based on the visible light spectrum
        if w >= 400 and w < 440:
            R = -(w - 440) / (440 - 400)
            G = 0
            B = 1
        elif w >= 440 and w < 490:
            R = 0
            G = (w - 440) / (490 - 440)
            B = 1
        elif w >= 490 and w < 510:
            R = 0
            G = 1
            B = -(w - 510) / (510 - 490)
        elif w >= 510 and w < 580:
            R = (w - 510) / (580 - 510)
            G = 1
            B = 0
        elif w >= 580 and w < 645:
            R = 1
            G = -(w - 645) / (645 - 580)
            B = 0
        elif w >= 645 and w <= 780:
            R = 1
            G = 0
            B = 0
        else:
            R = 0
            G = 0
            B = 0

        R *= 255
        G *= 255
        B *= 255

        return (R, G, B)

if __name__ == '__main__':
    # Get the sound file from the command line arguments
    if len(sys.argv) != 2:
        print('Error: missing argument')
        sys.exit(1)
    sound_file = sys.argv[1]

    app = QtWidgets.QApplication(sys.argv)
    ex = SoundVisualizer(sound_file)
    sys.exit(app.exec_())